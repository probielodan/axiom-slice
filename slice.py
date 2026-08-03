#!/usr/bin/env python3
import argparse
import os
import sys
import subprocess
import shutil
import time
import json
import orjson
import concurrent.futures
import re
from pathlib import Path
import logging
import requests

# Flask server imports
from tempfile import *
import threading
from queue import Queue, Empty
from flask import Flask, Response, request, jsonify, send_from_directory
from flask_cors import CORS

# --- Flask Server Setup ---
app = Flask(__name__)
CORS(app)  # Allows the React app to communicate with the server
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

RED = "\033[31m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
BLUE = "\033[34m"
CYAN = "\033[36m"
NC = "\033[0m"

if os.name == "nt":
    ORCASLICER_DIR = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming")) / "OrcaSlicer"
else:
    ORCASLICER_DIR = Path.home() / ".config" / "OrcaSlicer"
WORKING_DIR = os.name == "nt" and Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / "orca-slicer" or Path.home() / ".local" / "share" / "orca-slicer"
PROJECTS_DIR = WORKING_DIR / "projects"
CONFIG_DIR = WORKING_DIR / "configs"
MACHINE_DIR = CONFIG_DIR / "machine"
PROCESS_DIR = CONFIG_DIR / "process"
FILAMENT_DIR = CONFIG_DIR / "filament"
SCRIPT_DIR = Path(__file__).resolve().parent


# --- Common functions for both CLI and Server ---
def output(message, args=None):
    """Prints a message to the console and optionally to a log file or a stream generator."""
    if not args or not args.quiet:
        if args and args.stream_queue:
            args.stream_queue.put(message + "\n")
        else:
            print(message)
    message = re.sub(r"\x1B\[[0-9;]*[mGK]", "", message)
    if args and args.log_file:
        with open(args.log_file, "a", encoding="utf-8") as f:
            f.write(message + "\n")


def output_debug(level, message, args=None):
    """Prints a message only if the debug level is high enough."""
    if not args or args.debug_level >= level:
        output(message, args)


def flatten_configs(args=None):
    """
    Generates flattened configuration files for all profiles by preloading
    system profiles, caching flattened versions, and processing user
    profiles in parallel.
    """
    if not ORCASLICER_DIR.is_dir():
        output(f"{RED}❗ OrcaSlicer directory not found: {ORCASLICER_DIR}{NC}", args)
        output(f"{YELLOW}⚠️  Please ensure OrcaSlicer is installed and the directory exists.{NC}", args)
        return False

    output_debug(3, f"{BLUE}⚙️ Flattening configurations...{NC}", args)

    def _preload_system_profiles(system_dirs):
        """Loads all system profiles into memory in parallel."""
        lookup = {ptype: {} for ptype in ["filament", "machine", "process"]}
        data_store = {}
        paths_to_load = set()

        for sys_dir in system_dirs:
            vendor = sys_dir.name
            meta_json = sys_dir.with_suffix(".json")
            if not meta_json.exists():
                continue
            try:
                with open(meta_json, "rb") as f:
                    meta = orjson.loads(f.read())
            except Exception:
                continue

            for ptype, list_name in [("filament", "filament_list"), ("process", "process_list"), ("machine", "machine_list")]:
                for item in meta.get(list_name, []):
                    path = sys_dir / item["sub_path"]
                    paths_to_load.add(path)
                    lookup[ptype].setdefault(vendor, {})[item["name"]] = path

        def _load_json(path):
            try:
                with open(path, "rb") as f:
                    return path, orjson.loads(f.read())
            except Exception:
                return path, None

        with concurrent.futures.ThreadPoolExecutor() as executor:
            results = executor.map(_load_json, paths_to_load)

        for path, data in results:
            if data:
                data_store[path] = data

        return lookup, data_store

    def _get_flattened_profile(profile_name, profile_type, lookup, data_store, cache, vendor=None):
        cache_key = (profile_type, vendor, profile_name)
        if cache_key in cache:
            return cache[cache_key]

        if vendor not in lookup.get(profile_type, {}):
            return {}

        path = lookup[profile_type][vendor].get(profile_name)
        if not path or path not in data_store:
            return {}

        profile = data_store[path]
        parent_name = profile.get("inherits")

        if not parent_name:
            cache[cache_key] = profile
            return profile

        flattened_parent = _get_flattened_profile(parent_name, profile_type, lookup, data_store, cache, vendor)

        result = {**flattened_parent, **profile}
        cache[cache_key] = result
        return result

    user_dir = ORCASLICER_DIR / "user" / "default"
    system_dirs = [dir for dir in (ORCASLICER_DIR / "system").glob("*") if dir.is_dir()]

    lookup, data_store = _preload_system_profiles(system_dirs)

    flatten_cache = {}
    for ptype, vendors in lookup.items():
        for vendor, profiles in vendors.items():
            for name in profiles.keys():
                _get_flattened_profile(name, ptype, lookup, data_store, flatten_cache, vendor)

    for profile_type in ["filament", "machine", "process"]:
        input_dir = user_dir / profile_type
        output_dir = CONFIG_DIR / profile_type
        os.makedirs(output_dir, exist_ok=True)

        user_profile_paths = list(input_dir.glob("*.json"))

        def _process_user_file(path):
            try:
                with open(path, "rb") as f:
                    profile = orjson.loads(f.read())

                parent_name = profile.get("inherits")
                flattened_profile = {}
                if parent_name:
                    parent_path = lookup[profile_type]
                    for vendor, profiles in parent_path.items():
                        flattened_parent = {}
                        if parent_name in profiles:
                            flattened_parent = _get_flattened_profile(parent_name, profile_type, lookup, data_store, flatten_cache, vendor)
                            break
                    flattened_profile = {**flattened_parent, **profile}
                else:
                    flattened_profile = profile

                output_file = output_dir / path.name
                with open(output_file, "wb") as f:
                    f.write(orjson.dumps(flattened_profile, option=orjson.OPT_INDENT_2))
            except Exception as e:
                print(f"Failed to process {path}: {e}")

        with concurrent.futures.ThreadPoolExecutor() as executor:
            executor.map(_process_user_file, user_profile_paths)
    return True

# def find_inherited_profile(profile_type, inherit_name, system_dirs):
#     """Finds an inherited profile file by name in the system directories."""
#     for system_dir in system_dirs:
#         profile_path = system_dir / profile_type
#         for file in profile_path.glob("*.json"):
#             try:
#                 with open(file, "r") as f:
#                     data = json.load(f)
#                     if data.get("name") == inherit_name:
#                         return data
#             except Exception:
#                 continue
#     return None


# def flatten_profile(profile, profile_type, system_dirs):
#     """Recursively flattens an OrcaSlicer profile by applying inherited settings."""
#     flattened = dict(profile)
#     seen = set()
#     inherit_name = flattened.get("inherits")
#     while inherit_name and inherit_name not in seen:
#         seen.add(inherit_name)
#         inherited_profile = find_inherited_profile(profile_type, inherit_name, system_dirs)
#         if not inherited_profile:
#             break
#         for key, value in inherited_profile.items():
#             if key not in flattened:
#                 flattened[key] = value
#         inherit_name = inherited_profile.get("inherits")
#     return flattened


# def flatten_configs(args = None):
#     """Generates flattened configuration files for all profiles."""
#     if not ORCASLICER_DIR.is_dir():
#         output(f"{RED}❗ OrcaSlicer directory not found: {ORCASLICER_DIR}{NC}", args)
#         output(f"{YELLOW}⚠️  Please ensure OrcaSlicer is installed and the directory exists.{NC}", args)
#         return False
#     output_debug(3, f"{BLUE}⚙️ Flattening configurations...{NC}", args)
#     user_dir = ORCASLICER_DIR / "user" / "default"
#     system_dirs = list((ORCASLICER_DIR / "system").glob("*"))
#     for profile_type in ["filament", "machine", "process"]:
#         os.makedirs(CONFIG_DIR / profile_type, exist_ok=True)
#         input_dir = user_dir / profile_type
#         output_dir = CONFIG_DIR / profile_type
#         for file in input_dir.glob("*.json"):
#             try:
#                 with open(file, "r") as f:
#                     profile = json.load(f)
#                 flattened = flatten_profile(profile, profile_type, system_dirs)
#                 output_file = output_dir / file.name
#                 with open(output_file, "w") as f:
#                     json.dump(flattened, f, indent=2)
#             except Exception as e:
#                 output_debug(2, f"{YELLOW}⚠️  Warning: Error processing {file.name}: {e}{NC}", args)
#     return True



def list_available_configs(args=None):
    """Lists all available configuration files."""
    output(f"{CYAN}📦 Available machine configs:{NC}", args)
    for f in os.listdir(MACHINE_DIR):
        output(f, args)
    output(f"{CYAN}📦 Available process configs:{NC}", args)
    for f in os.listdir(PROCESS_DIR):
        output(f, args)
    output(f"{CYAN}📦 Available filament configs:{NC}", args)
    for f in os.listdir(FILAMENT_DIR):
        output(f, args)


def upload_to_printer(gcode_files, args=None):
    """Uploads G-code files to a Moonraker printer."""
    success = True
    filenames = ""
    output_debug(3, f"{BLUE}⬆️ Uploading G-code files to {args.printer}...{NC}", args)
    for file in gcode_files:
        output_debug(4, f"{BLUE} - Uploading file: {file}{NC}", args)
        try:
            uri = f"http://{args.printer}/server/files/upload"
            with open(file, "rb") as f:
                files = {"file": (os.path.basename(file), f)}
                requests.post(uri, files=files, timeout=15)
        except Exception:
            output_debug(1, f"{RED}❗ Failed to upload {file} to the printer.{NC}", args)
            success = False
            break
        filename = os.path.basename(file)
        filenames += filename + ","
    if success:
        output(f"{GREEN}✅ All G-code files uploaded successfully.{NC}", args)
        filenames = filenames.rstrip(",")
    else:
        return False, ""
    return True, filenames


def start_print_job(filenames, args=None):
    """Starts a print job on a Moonraker printer."""
    output_debug(3, f"{BLUE}🖨️ Starting print job for: {filenames}{NC}", args)
    try:
        requests.post(f"http://{args.printer}/server/job_queue/job?filenames={filenames}", timeout=15)
        requests.post(f"http://{args.printer}/server/job_queue/start", timeout=15)
    except Exception:
        output_debug(1, f"{RED}❗ Failed to start the print job.{NC}", args)
        return False
    output(f"{GREEN}✅ Print job started successfully.{NC}", args)
    return True


# --- Core Slicing Logic (refactored from main) ---
def run_slicer(args):
    """Encapsulates the core slicing functionality."""
    if not args:
        output(f"{RED}❗ Error: No arguments received!{NC}")
        return False
    if not args.models:
        output(f"{RED}❗ Error: No model file(s) provided.{NC}", args)
        return False

    project_name = args.project_name
    if not project_name or project_name == "project":
        project_name = Path(args.models[0]).stem
        if len(args.models) > 1:
            project_name += "_combined"
    project_dir = Path(args.output_dir) / f"{project_name}_{time.strftime('%Y-%m-%d_%H-%M-%S')}"
    project_dir.mkdir(parents=True, exist_ok=True)

    log_file = None
    if args.log_to_file:
        log_file = str(project_dir / f"{project_name}.log")
        args.log_file = log_file
        output(f"{CYAN}📝 Output will also be logged to: {log_file}{NC}", args)

    if args.start_print and not args.printer:
        output(f"{RED}❗ Error: --printer is required when using --print.{NC}", args)
        return False

    # if args.printer and re.match(r"^[a-zA-Z0-9\.-]+:[0-9]+$", args.printer):
    #     output(f"{RED}❗ Error: Printer address should not contain a port number.{NC}", args)
    #     return False

    if not args.stream_queue:
        if not flatten_configs(args):
            return False

    if not (MACHINE_DIR / args.machine).is_file():
        output(f"{RED}❗ Machine config not found: {args.machine}{NC}", args)
        if not args.quiet:
            list_available_configs(args)
        return False
    if not (PROCESS_DIR / args.process).is_file():
        output(f"{RED}❗ Process config not found: {args.process}{NC}", args)
        if not args.quiet:
            list_available_configs(args)
        return False
    if not (FILAMENT_DIR / args.filament).is_file():
        output(f"{RED}❗ Filament config not found: {args.filament}{NC}", args)
        if not args.quiet:
            list_available_configs(args)
        return False

    if args.orca_bin:
        orca_bin = args.orca_bin
    elif os.environ.get("ORCA_BIN"):
        orca_bin = os.environ["ORCA_BIN"]
    elif os.name == "nt":
        pf = os.environ.get("ProgramFiles", r"C:\Program Files")
        pf86 = os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")
        possible_bins = [
            os.path.join(pf, "OrcaSlicer", "orca-slicer.exe"),
            os.path.join(pf86, "OrcaSlicer", "orca-slicer.exe"),
            "orca-slicer.exe",
        ]
        for bin_path in possible_bins:
            if os.path.isfile(bin_path):
                orca_bin = bin_path
                break
        else:
            orca_bin = "orca-slicer.exe"
    else:
        orca_bin = str(Path("orca-slicer"))

    cmd = [orca_bin, "--allow-newer-file", "--allow-rotations"]
    if args.auto_orient:
        cmd += ["--orient", "1"]
    cmd += [
        "--arrange", "1",
        "--ensure-on-bed",
        "--slice", "0",
        "--debug", str(args.debug_level),
        "--load-settings", f"{MACHINE_DIR / args.machine};{PROCESS_DIR / args.process}",
        "--load-filaments", str(FILAMENT_DIR / args.filament),
        "--outputdir", str(project_dir),
    ]
    if ORCASLICER_DIR.exists():
        cmd += ["--datadir", str(ORCASLICER_DIR)]
    if args.info:
        cmd += ["--info"]
    if args.export_3mf:
        export_3mf_filename = f"{project_name}.3mf"
        cmd += ["--export-3mf", export_3mf_filename]
    if args.export_settings:
        export_settings_path = project_dir / "settings.json"
        cmd += ["--export-settings", str(export_settings_path)]
    cmd += [str(item) for item in args.slicer_args]
    cmd += args.models

    output_debug(4, f"{BLUE}💡 OrcaSlicer command:{NC}", args)
    output_debug(4, " ".join([f"\"{string}\"" if " " in string else string for string in cmd]), args)

    if args.dry_run:
        output(f"{BLUE}💡 OrcaSlicer command:{NC}", args)
        output(" ".join([f"\"{string}\"" if " " in string else string for string in cmd]), args)
        output(f"{CYAN}🧪 Dry run: command not executed.{NC}", args)
        return True

    output(f"{CYAN}⏳ Slicing...{NC}", args)

    status = 1
    try:
        with subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding='utf-8') as process:
            for line in process.stdout:
                if args.show_output:
                    output(line.strip(), args)
            process.wait()
            status = process.returncode
    except FileNotFoundError:
        output(f"{RED}❗ OrcaSlicer binary not found. Please check your path or specify it with --orca-bin.{NC}", args)
        return False
    except Exception as e:
        output(f"{RED}❗ Failed to run OrcaSlicer: {e}{NC}", args)
        return False

    if status != 0:
        output(f"{RED}❗ OrcaSlicer failed to slice the model(s).{NC}", args)
        result_path = project_dir / "result.json"
        if result_path.is_file():
            with open(result_path, "r", encoding="utf-8") as f:
                try:
                    result_json = json.load(f)
                    error_string = result_json.get("error_string")
                    output(f"{RED}❗ Error from OrcaSlicer: {error_string}{NC}", args)
                except Exception:
                    pass
        return False

    output(f"{GREEN}✅ Slicing completed successfully.{NC}", args)

    if args.print_results:
        output(f"{CYAN}📊 Slicing Results:{NC}", args)
        result_path = project_dir / "result.json"
        if result_path.is_file():
            with open(result_path, "r", encoding="utf-8") as f:
                try:
                    result_json = json.load(f)
                    for plate_info in result_json.get("sliced_plates", []):
                        if plate_info.get("warning_message"):
                            output_debug(2, f"{YELLOW}⚠️  --- Plate {plate_info.get('id')} Warning ---{NC}", args)
                            output_debug(2, f"{YELLOW}{plate_info.get('warning_message')}{NC}", args)
                    error_string = result_json.get("error_string")
                    if error_string != "Success.":
                        output_debug(1, f"{RED}❗ Overall Error: {error_string}{NC}", args)
                except Exception:
                    output_debug(2, f"{YELLOW}⚠️  Warning: result.json not found or unreadable.{NC}", args)
        else:
            output_debug(2, f"{YELLOW}⚠️  Warning: result.json not found.{NC}", args)

    gcode_files = list(project_dir.glob("*.gcode"))
    gcode_count = len(gcode_files)
    for gcode_file in gcode_files:
        m = re.search(r"plate_(\d+)\.gcode", gcode_file.name)
        if m:
            plate_number = m.group(1)
            if gcode_count > 1:
                new_name = project_dir / f"{project_name}_plate_{plate_number}.gcode"
            else:
                new_name = project_dir / f"{project_name}.gcode"
            output_debug(3, f"{BLUE}🔄 Renaming: {gcode_file} → {new_name}{NC}", args)
            gcode_file.rename(new_name)

    if args.printer:
        gcode_files = list(project_dir.glob("*.gcode"))
        success, filenames = upload_to_printer([str(f) for f in gcode_files], args)
        if success and args.start_print:
            start_print_job(filenames, args)

    if not args.keep_result:
        output_debug(3, f"{BLUE}🗑️ Removing result.json{NC}", args)
        try:
            os.remove(project_dir / "result.json")
        except Exception:
            pass

    if (args.keep_project or args.export_3mf or args.export_settings or args.log_to_file or args.keep_result):
        output_debug(3, f"{BLUE}📁 Project directory: {project_dir}{NC}", args)
    else:
        shutil.rmtree(project_dir, ignore_errors=True)
        output_debug(3, f"{BLUE}🧹 Project directory removed.{NC}", args)

    return True


# --- Flask Server Routes and Logic ---
STATE_PATH = WORKING_DIR / "slicer-state.json"
DEFAULT_STATE_PATH = Path(__file__).resolve().parent / "default-slicer-state.json"


class SlicerArgs:
    def __init__(self, **kwargs):
        for key, value in kwargs.items():
            setattr(self, key, self.smart_cast(value))

    def parse_list(self, json_string):
        json_string = json_string.replace("false", "False").replace("true", "True")
        parsed_list = json.loads(json_string)
        return parsed_list

    def smart_cast(self, value: str):
        try:
            if value.startswith("[") and value.endswith("]"):
                return self.parse_list(value)
            if value.lower() == "true":
                return True
            if value.lower() == "false":
                return False
            if "." in value:
                return float(value)
            return int(value)
        except:
            return value


def _ensure_config_file_exists():
    """Ensures state exists, restores default state if not"""
    if not STATE_PATH.exists():
        if DEFAULT_STATE_PATH.exists():
            STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(DEFAULT_STATE_PATH, STATE_PATH)
        else:
            raise FileNotFoundError(f"Default config file not found!")


@app.route("/api/slicer-state", methods=["GET"])
def get_slicer_state():
    """
    Provides the slicer state JSON.
    Resets to default if it's missing or corrupted.
    """
    try:
        with STATE_PATH.open("r", encoding="utf-8") as f:
            return jsonify(json.load(f))
    except (FileNotFoundError, json.JSONDecodeError) as e:
        _ensure_config_file_exists()
        try:
            with STATE_PATH.open("r", encoding="utf-8") as f:
                return jsonify(json.load(f))
        except (FileNotFoundError, json.JSONDecodeError):
            return jsonify({"error": "slicer-state.json is invalid, and the default is missing or invalid"}), 500


@app.route("/api/slicer-state", methods=["POST"])
def update_slicer_state():
    """
    Updates the slicer state JSON.
    Replaces the config with the default + posted changes if it's missing.
    """
    try:
        with STATE_PATH.open("r", encoding="utf-8") as f:
            data = json.load(f)
        data.update(request.json)
        with STATE_PATH.open("w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        return jsonify({"success": True})
    except (FileNotFoundError, json.JSONDecodeError) as e:
        try:
            _ensure_config_file_exists()
            with STATE_PATH.open("r", encoding="utf-8") as f:
                data = json.load(f)
            data.update(request.json)
            with STATE_PATH.open("w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            return jsonify({"success": True, "message": "Missing or corrupted state replaced and updated."})
        except Exception as e:
            return jsonify({"error": f"Failed to recover missing or corrupted state: {e}"}), 500


@app.route("/api/configs", methods=["GET"])
def get_configs():
    """Scans for available config files and returns their names."""
    if not flatten_configs():
        return jsonify({"error": f"OrcaSlicer directory is missing"}), 500

    config_paths = {
        "machine": MACHINE_DIR,
        "filament": FILAMENT_DIR,
        "process": PROCESS_DIR,
    }

    configs = []
    for key, path in config_paths.items():
        try:
            if path.exists() and path.is_dir():
                configs.append({key: sorted([f.name for f in path.iterdir() if f.suffix == ".json"])})
            else:
                configs.append({key: []})
        except Exception as e:
            configs.append({key: []})
    return jsonify(configs)


@app.route("/api/slice", methods=["POST"])
def slice_model():
    """Handles a POST request from the UI to slice a model and streams output."""
    if "models" not in request.files:
        return jsonify({"error": "No model file(s) provided."}), 400

    models_files = request.files.getlist("models")
    form_data = request.form
    model_paths = []
    tmp_dir = TemporaryDirectory()
    for model_file in models_files:
        model_path = Path(tmp_dir.name) / model_file.filename

        try:
            with open(model_path, "wb") as f:
                f.write(model_file.read())
            model_paths.append(str(model_path))
        except Exception as e:
            output(f"{RED}An error occurred while saving the model file: {e}{NC}")
            return jsonify({"error": f"Failed to save model: {e}"}), 500

    stream_queue = Queue()
    slicer_args = SlicerArgs(
        models=model_paths,
        output_dir=PROJECTS_DIR,
        **form_data.to_dict(flat=True),
        orca_bin=None,
        log_file=None,
        stream_queue=stream_queue,
    )

    def slicer_thread_target():
        try:
            run_slicer(slicer_args)
        except Exception as e:
            output(f"{RED}An error occurred during slicing: {e}{NC}", slicer_args)
        finally:
            slicer_args.stream_queue.put(None)
            tmp_dir.cleanup()

    slicer_thread = threading.Thread(target=slicer_thread_target)
    slicer_thread.start()

    def response_generator():
        while True:
            try:
                line = slicer_args.stream_queue.get(timeout=1)
                if line is None:
                    break
                yield line
            except Empty:
                if not slicer_thread.is_alive():
                    break

    return Response(response_generator(), mimetype="text/plain")


@app.route("/", defaults={"path": ""})
@app.route("/<path:path>")
def serve_frontend(path):
    if path != "" and os.path.exists(os.path.join(SCRIPT_DIR / "frontend/dist", path)):
        return send_from_directory(SCRIPT_DIR / "frontend/dist", path)
    else:
        return send_from_directory(SCRIPT_DIR / "frontend/dist", "index.html")


# --- Main entry point and CLI logic ---
def main():
    usage = f"""{CYAN}📜 slice.py [OPTIONS] <model.stl|.3mf ...>{NC}
"""
    usage_epilogue = f"""
You can override the OrcaSlicer binary by setting the ORCA_BIN environment variable.
Any additional arguments will be passed directly to OrcaSlicer.
    Run a successful slice with --export-settings to see a list of available OrcaSlicer options.

Examples:
    python slice.py model.3mf --export-3mf --export-settings --info
    python slice.py --no-orient --debug 5 model.stl
    python slice.py --list-configs
    python slice.py --project-name myprint --dry-run model.stl
    python slice.py model.stl --print-results --log-to-file
    python slice.py model.stl --quiet
    python slice.py model.stl --printer 192.168.1.10 --print

    python slice.py --server
"""
    parser = argparse.ArgumentParser(
        description="Slice 3D models using OrcaSlicer CLI or run as a web server.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=usage_epilogue,
        prog="slice.py",
        usage=usage,
    )

    # Web server
    parser.add_argument("--server", action="store_true", help="Start the web server.")
    # Config selection
    parser.add_argument("--filament", default="Creality Generic PETG - Custom.json", help="Use a different filament config (from configs/filament)")
    parser.add_argument("--machine", default="Creality K1 (0.4 nozzle) - Custom.json", help="Use a different machine config (from configs/machine)")
    parser.add_argument("--process", default="0.16mm Optimal @Creality K1 (0.4 nozzle) - Custom.json", help="Use a different process config (from configs/process)")
    # Slicing options
    parser.add_argument("--auto-orient", action="store_true", help="Enable auto-orient")
    parser.add_argument("--info", action="store_true", help="Print model information")
    parser.add_argument("--dry-run", action="store_true", help="Print the OrcaSlicer command but don't run it")
    parser.add_argument("--export-3mf", action="store_true", help="Export project as 3MF (implies --keep-project)")
    parser.add_argument("--export-settings", action="store_true", help="Export settings to a file (implies --keep-project)")
    parser.add_argument("--project-name", default="project", help="Set the base name for the output project")
    # Output and logging
    parser.add_argument("--output-dir", default=PROJECTS_DIR, help="Set a custom output directory")
    parser.add_argument("--keep-project", action="store_true", help="Keep the project directory after slicing")
    parser.add_argument("--keep-result", action="store_true", help="Keep the result.json file (implies --keep-project)")
    parser.add_argument("--print-results", action="store_true", help="Output slicing results summary from result.json")
    parser.add_argument("--show-output", action="store_true", help="Show the raw OrcaSlicer output in the console")
    parser.add_argument("--log-to-file", action="store_true", help="Log all script and OrcaSlicer output to a file (implies --keep-project)")
    parser.add_argument("--quiet", default=False, action="store_true", help="Suppress all non-essential script output")
    # Config management
    parser.add_argument("--flatten", action="store_true", help="Generate flattened OrcaSlicer config files and exit")
    parser.add_argument("--list-configs", action="store_true", help="Generate flattened configs and list available options")
    # Printer
    parser.add_argument("--printer", help="Upload gcode to a Moonraker printer (e.g., 192.168.1.10)")
    parser.add_argument("--print", dest="start_print", action="store_true", help="Start a print job after uploading to the printer (requires --printer)")
    # Debug/advanced
    parser.add_argument("--debug_level", type=int, default=0, help="Set debug level 0:fatal, 1:error, 2:warning, 3:info, 4:debug, 5:trace")
    parser.add_argument("--orca-bin", default=None, help="Override the OrcaSlicer binary path")

    args, slicer_args = parser.parse_known_args()
    extra_args = []
    models = []

    i = 0
    while i < len(slicer_args):
        token = slicer_args[i]
        if token.startswith("--"):
            if i + 1 >= len(slicer_args) or slicer_args[i + 1].startswith("--"):
                extra_args.append(token)
                i += 1
            else:
                extra_args.extend([token, slicer_args[i + 1]])
                i += 2
        else:
            models.append(token)
            i += 1

    args.slicer_args = extra_args
    args.models = models

    if args.server:
        app.run(host="0.0.0.0")
    else:
        if args.list_configs or args.flatten:
            if flatten_configs(args):
                if args.list_configs:
                    list_available_configs(args)
            sys.exit(0)

        try:
            args.log_file = None
            args.stream_queue = None
            if not run_slicer(args):
                sys.exit(1)
        except KeyboardInterrupt:
            output(f"\r{RED}❗ Now why would you do a damn thing like that{NC}")


if __name__ == "__main__":
    main()
