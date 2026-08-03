#!/bin/bash

# ANSI color codes for output
RED='\e[31m'
GREEN='\e[32m'
YELLOW='\e[33m'
BLUE='\e[34m'
CYAN='\e[36m'
NC='\e[0m' # No Color

# Configuration directories
SCRIPT_DIR=$(dirname "$0")
FLATTEN_SCRIPT="$SCRIPT_DIR/flatten.py"
ORCASLICER_DIR="$HOME/.config/OrcaSlicer"
CONFIG_DIR="$SCRIPT_DIR/flattened_configs"
FILAMENT_DIR="$CONFIG_DIR/filament"
MACHINE_DIR="$CONFIG_DIR/machine"
PROCESS_DIR="$CONFIG_DIR/process"
PROJECTS_DIR="$SCRIPT_DIR/projects"

# Default configuration files
DEFAULT_FILAMENT="Creality Generic PETG - Custom.json"
DEFAULT_MACHINE="Creality K1 (0.4 nozzle) - Custom.json"
DEFAULT_PROCESS="0.16mm Optimal @Creality K1 (0.4 nozzle) - Custom.json"

# Script settings
DEBUG_LEVEL=0
ENABLE_ORIENT=0
DRY_RUN=0
KEEP_PROJECT=0
EXPORT_3MF=0
EXPORT_SETTINGS=0
OUTPUT_INFO=0
KEEP_RESULT=0
OUTPUT_RESULTS=0
LOG_TO_FILE=0
SHOW_ORCA_OUTPUT=0
QUIET_MODE=0
PROJECT_NAME="project"
PRINTER_ADDRESS=""
START_PRINT=0

usage() {
    cat <<EOF
📜 Usage: $(basename "$0") [OPTIONS] <model.stl|.3mf ...>

Slices 3D models using OrcaSlicer CLI with custom flattened configs.

OPTIONS:
    --list-configs          Generate flattened configs and list available options
    --filament NAME.json    Use a different filament config (from flattened/filament)
    --machine NAME.json     Use a different machine config (from flattened/machine)
    --process NAME.json     Use a different process config (from flattened/process)
    --auto-orient           Enable auto-orient
    --dry-run               Print the OrcaSlicer command but don't run it
    --export-3mf            Export project as 3MF (default: off)
                            (implies --keep-project)
    --export-settings       Export settings to a file (default: off)
                            (implies --keep-project)
    --info                  Print model information (default: off)
    --output-results        Output slicing results summary from result.json (default: off)
    --project-name NAME     Set the base name for the output project (default: project)
    --keep-project          Keep the project directory after slicing (default: off)
    --keep-result           Keep the result.json file (default: off)
                            (implies --keep-project)
    --flatten               Generate flattened OrcaSlicer config files and exit
    --log-to-file           Log all script and OrcaSlicer output to a file (default: off)
                            (implies --keep-project)
    --show-output           Show the raw OrcaSlicer output in the console (default: off)
    --quiet                 Suppress all non-essential script output (default: off)
    --debug N               Set debug level 0:fatal, 1:error, 2:warning, 3:info, 4:debug, 5:trace(default: 0)
    --printer ADDR          Upload gcode to a Moonraker printer (e.g., 192.168.1.10)
    --print                 Start a print job after uploading to the printer (requires --printer)
    -h, --help              Show this help message

You can override the OrcaSlicer binary by setting the ORCA_BIN environment variable.
Any additional arguments will be passed directly to OrcaSlicer.
    Run a successful slice with --export-settings to see a list of available OrcaSlicer options.

Examples:
    ./slice.sh model.3mf --export-3mf --export-settings --info
    ./slice.sh --no-orient --debug 5 model.stl
    ./slice.sh --list-configs
    ./slice.sh --project-name myprint --dry-run model.stl
    ./slice.sh model.stl --output-results --log-to-file
    ./slice.sh model.stl --quiet
    ./slice.sh model.stl --printer 192.168.1.10 --print
EOF
}

output() {
    local message="$*"
    if [[ $QUIET_MODE -eq 0 ]]; then
        echo -e "$message"
    fi
    # Log to file, stripping color codes
    if [[ $LOG_TO_FILE -eq 1 ]]; then
        echo -e "$message" | sed -r "s/\x1B\[([0-9]{1,2}(;[0-9]{1,2})?)?[mGK]//g" >> "$LOG_FILE"
    fi
}

output_debug() {
    local level="$1"
    shift
    local message="$*"
    if [[ $DEBUG_LEVEL -ge $level ]]; then
        output "$message"
    fi
}

flatten_configs() {
    output_debug 3 "${YELLOW}⚙️  Flattening configurations...${NC}"
    if ! python3 "$FLATTEN_SCRIPT"; then
        output "${RED}❗ Failed to flatten configurations. Exiting.${NC}"
        exit 1
    fi
}

list_available_configs() {
    output "${BLUE}📦 Available machine configs:${NC}"
    ls -1 "$MACHINE_DIR"
    output "${BLUE}📦 Available process configs:${NC}"
    ls -1 "$PROCESS_DIR"
    output "${BLUE}📦 Available filament configs:${NC}"
    ls -1 "$FILAMENT_DIR"
}

upload_to_printer() {
    local gcode_files=("$@")
    local success=1
    filenames=""

    output_debug 3 "${BLUE}⬆️ Uploading G-code files to $PRINTER_ADDRESS...${NC}"

    for file in "${gcode_files[@]}"; do
        output_debug 4 "${CYAN}  - Uploading file: $file${NC}"
        if ! curl -sS -X POST "http://$PRINTER_ADDRESS/server/files/upload" \
            -F "file=@$file" > /dev/null; then
            output "${RED}❗ Failed to upload $file to the printer.${NC}"
            success=0
            break
        fi
        filename=$(basename "$file")
        filenames="$filenames$filename,"
    done

    if [[ $success -eq 1 ]]; then
        output "${GREEN}✅ All G-code files uploaded successfully.${NC}"
        filenames="${filenames%,}"
    else
        return 1
    fi
}

start_print_job() {
    local filenames="$1"
    output_debug 3 "${BLUE}🖨️ Starting print job for: $filenames${NC}"
    if ! curl -sS -X POST "http://$PRINTER_ADDRESS/server/job_queue/job?filenames=$filenames" > /dev/null; then
        output "${RED}❗ Failed to start the print job.${NC}"
        return 1
    fi
    output "${GREEN}✅ Print job started successfully.${NC}"
}

POSITIONAL_ARGS=()
EXTRA_ORCA_ARGS=()

# Parse args
while [[ $# -gt 0 ]]; do
    case "$1" in
        --debug)
            DEBUG_LEVEL="$2"
            shift 2
            ;;
        --auto-orient)
            ENABLE_ORIENT=1
            shift
            ;;
        --filament)
            DEFAULT_FILAMENT="$2"
            shift 2
            ;;
        --machine)
            DEFAULT_MACHINE="$2"
            shift 2
            ;;
        --process)
            DEFAULT_PROCESS="$2"
            shift 2
            ;;
        --project-name)
            PROJECT_NAME="$2"
            shift 2
            ;;
        --dry-run)
            DRY_RUN=1
            shift
            ;;
        --keep-project)
            KEEP_PROJECT=1
            shift
            ;;
        --keep-result)
            KEEP_RESULT=1
            KEEP_PROJECT=1
            shift
            ;;
        --export-3mf)
            EXPORT_3MF=1
            KEEP_PROJECT=1
            shift
            ;;
        --export-settings)
            EXPORT_SETTINGS=1
            KEEP_PROJECT=1
            shift
            ;;
        --info)
            OUTPUT_INFO=1
            shift
            ;;
        --output-results)
            OUTPUT_RESULTS=1
            shift
            ;;
        --log-to-file)
            LOG_TO_FILE=1
            KEEP_PROJECT=1
            shift
            ;;
        --show-output)
            SHOW_ORCA_OUTPUT=1
            shift
            ;;
        --quiet)
            QUIET_MODE=1
            shift
            ;;
        --printer)
            PRINTER_ADDRESS="$2"
            shift 2
            ;;
        --print)
            START_PRINT=1
            shift
            ;;
        --list-configs)
            flatten_configs
            list_available_configs
            exit 0
            ;;
        --flatten)
            flatten_configs
            exit 0
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        --*)
            EXTRA_ORCA_ARGS+=("$1")
            shift
            ;;
        *)
            POSITIONAL_ARGS+=("$1")
            shift
            ;;
    esac
done

if [[ ${#POSITIONAL_ARGS[@]} -eq 0 ]]; then
    output "${RED}❗ Error: No model file(s) provided.${NC}\n"
    usage
    exit 1
fi

if [[ $START_PRINT -eq 1 && -z "$PRINTER_ADDRESS" ]]; then
    output "${RED}❗ Error: --printer is required when using --print.${NC}\n"
    usage
    exit 1
fi

if [[ -n "$PRINTER_ADDRESS" && "$PRINTER_ADDRESS" =~ ^[a-zA-Z0-9\.-]+:[0-9]+$ ]]; then
    output "${RED}❗ Error: Printer address should not contain a port number.${NC}\n"
    usage
    exit 1
fi

# Set output filename based on first positional argument if not overridden
if [[ "$PROJECT_NAME" == "project" ]]; then
    PROJECT_NAME=$(basename "${POSITIONAL_ARGS[0]%.*}")
    if [[ ${#POSITIONAL_ARGS[@]} -gt 1 ]]; then
        PROJECT_NAME="${PROJECT_NAME}_combined"
    fi
fi
PROJECT_DIR="$PROJECTS_DIR/${PROJECT_NAME}_$(date +%Y-%m-%d_%H-%M-%S)"
mkdir -p "$PROJECT_DIR"

if [[ $LOG_TO_FILE -eq 1 ]]; then
    LOG_FILE="$PROJECT_DIR/${PROJECT_NAME}.log"
    mkdir -p "$PROJECT_DIR"
    output "${CYAN}📝 Output will also be logged to: ${LOG_FILE}${NC}"
else
    trap 'output "${YELLOW}💥 Interrupted. Cleaning up.${NC}"; rm -rf "$PROJECT_DIR"; exit 1' INT TERM
fi

if [[ ! -d "$ORCASLICER_DIR" ]]; then
    output "${RED}❗ OrcaSlicer directory not found: $ORCASLICER_DIR${NC}"
    output "${YELLOW}⚠️ Please ensure OrcaSlicer is installed and the directory exists.${NC}"
    exit 1
fi

if [[ $QUIET_MODE -eq 0 ]]; then
    flatten_configs
fi

if [[ ! -f "$FILAMENT_DIR/$DEFAULT_FILAMENT" ]]; then
    output "${RED}❗ Filament config not found: $DEFAULT_FILAMENT${NC}"
    if [[ $QUIET_MODE -eq 0 ]]; then
        list_available_configs
    fi
    exit 1
fi
if [[ ! -f "$MACHINE_DIR/$DEFAULT_MACHINE" ]]; then
    output "${RED}❗ Machine config not found: $DEFAULT_MACHINE${NC}"
    if [[ $QUIET_MODE -eq 0 ]]; then
        list_available_configs
    fi
    exit 1
fi
if [[ ! -f "$PROCESS_DIR/$DEFAULT_PROCESS" ]]; then
    output "${RED}❗ Process config not found: $DEFAULT_PROCESS${NC}"
    if [[ $QUIET_MODE -eq 0 ]]; then
        list_available_configs
    fi
    exit 1
fi

# Build command
ORCA_BIN="${ORCA_BIN:-./orca-slicer}"
CMD=("$ORCA_BIN")
CMD+=(--allow-newer-file)
CMD+=(--allow-rotations)
[[ $ENABLE_ORIENT -eq 1 ]] && CMD+=(--orient 1)
CMD+=(--arrange 1)
CMD+=(--ensure-on-bed)
CMD+=(--datadir "$ORCASLICER_DIR")
CMD+=(--load-settings "$MACHINE_DIR/$DEFAULT_MACHINE;$PROCESS_DIR/$DEFAULT_PROCESS")
CMD+=(--load-filaments "$FILAMENT_DIR/$DEFAULT_FILAMENT")
CMD+=(--outputdir "$PROJECT_DIR")
[[ $EXPORT_3MF -eq 1 ]] && CMD+=(--export-3mf "${PROJECT_NAME}.3mf")
[[ $EXPORT_SETTINGS -eq 1 ]] && CMD+=(--export-settings "$PROJECT_DIR/settings.json")
[[ $OUTPUT_INFO -eq 1 ]] && CMD+=(--info)
CMD+=(--slice 0)
CMD+=(--debug "$DEBUG_LEVEL")
CMD+=("${POSITIONAL_ARGS[@]}")
CMD+=("${EXTRA_ORCA_ARGS[@]}")

output_debug 4 "${BLUE}💡 OrcaSlicer command:${NC}"
output_debug 4 "${CMD[@]}"

if [[ $DRY_RUN -eq 1 ]]; then
    output "${CYAN}🧪 Dry run: command not executed.${NC}"
    exit 0
fi

if [[ $QUIET_MODE -eq 0 && $SHOW_ORCA_OUTPUT -eq 0 ]]; then
    output "${YELLOW}⏳ Slicing...${NC}"
fi

if [[ $LOG_TO_FILE -eq 1 ]]; then
    if [[ $SHOW_ORCA_OUTPUT -eq 1 ]]; then
        # Show output and log to file with colors stripped
        "${CMD[@]}" 2>&1 | tee -a >(sed -r "s/\x1B\[([0-9]{1,2}(;[0-9]{1,2})?)?[mGK]//g" >> "$LOG_FILE")
    else
        # Log to file with colors stripped, suppress console output
        "${CMD[@]}" &>> "$LOG_FILE"
    fi
elif [[ $SHOW_ORCA_OUTPUT -eq 1 ]]; then
    "${CMD[@]}"
else
    "${CMD[@]}" &>/dev/null
fi
STATUS=$?

if [[ $STATUS -ne 0 ]]; then
    output "${RED}❗ OrcaSlicer failed to slice the model(s).${NC}"
    if [[ -f "$PROJECT_DIR/result.json" ]]; then
        ERROR_STRING=$(jq -r '.error_string' "$PROJECT_DIR/result.json")
        output "${RED}❗ Error from OrcaSlicer: ${ERROR_STRING}${NC}"
    fi
    exit 1
fi

output "${GREEN}✅ Slicing completed successfully.${NC}"

if [[ $OUTPUT_RESULTS -eq 1 ]]; then
    output "${BLUE}📊 Slicing Results:${NC}"
    if [[ -f "$PROJECT_DIR/result.json" ]]; then
        jq -c '.sliced_plates[]' "$PROJECT_DIR/result.json" | while read -r plate_info; do
            PLATE_ID=$(echo "$plate_info" | jq -r '.id')
            WARNING_MESSAGE=$(echo "$plate_info" | jq -r '.warning_message')

            if [[ -n "$WARNING_MESSAGE" ]]; then
                output "${YELLOW}⚠️ --- Plate ${PLATE_ID} Warning ---${NC}"
                output "${YELLOW}${WARNING_MESSAGE}${NC}"
            fi
        done
        ERROR_STRING=$(jq -r '.error_string' "$PROJECT_DIR/result.json")
        if [[ "$ERROR_STRING" != "Success." ]]; then
            output "${RED}❗ Overall Error: ${ERROR_STRING}${NC}"
        fi
    else
        output "${YELLOW}⚠️ Warning: result.json not found.${NC}"
    fi
fi

gcode_files=("$PROJECT_DIR"/*.gcode)
gcode_count=${#gcode_files[@]}

for gcode_file in "${gcode_files[@]}"; do
    plate_number=$(basename "$gcode_file" | grep -oP '(?<=plate_)\d+(?=\.gcode)')

    if [[ -n "$plate_number" ]]; then
        if [[ "$gcode_count" -gt 1 ]]; then
            new_name="${PROJECT_DIR}/${PROJECT_NAME}_plate_${plate_number}.gcode"
        else
            new_name="${PROJECT_DIR}/${PROJECT_NAME}.gcode"
        fi

        output_debug 3 "${YELLOW}🔄 Renaming: $gcode_file → $new_name${NC}"
        mv "$gcode_file" "$new_name"
    fi
done

if [[ -n "$PRINTER_ADDRESS" ]]; then
    renamed_gcode_files=("$PROJECT_DIR"/*.gcode)
    upload_to_printer "${renamed_gcode_files[@]}"
    if [[ $? -eq 0 && $START_PRINT -eq 1 ]]; then
        start_print_job "$filenames"
    fi
fi

if [[ $KEEP_RESULT -eq 0 ]]; then
    output_debug 3 "${YELLOW}🗑️  Removing result.json${NC}"
    rm -f "$PROJECT_DIR/result.json"
fi

if [[ $KEEP_PROJECT -eq 1 ]]; then
    output_debug 3 "${YELLOW}📁 Project directory: $PROJECT_DIR${NC}"
else
    rm -rf "$PROJECT_DIR"
    output_debug 3 "${YELLOW}🧹 Project directory removed.${NC}"
fi
