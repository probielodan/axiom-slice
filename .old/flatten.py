import os
import json
from pathlib import Path

# Set base directories
home_dir = Path.home()
orcaslicer_dir = home_dir / ".config" / "OrcaSlicer"
input_dirs = [orcaslicer_dir / "user" / "default",
            #   orcaslicer_dir / "system" / "Creality",
              ]
system_dirs = list((orcaslicer_dir / "system").glob("*"))
flattened_dir = Path("flattened_configs")

# Create flattened output dirs
for config_type in ["filament", "machine", "process"]:
    os.makedirs(flattened_dir / config_type, exist_ok=True)

def find_inherited_profile(profile_type, inherit_name):
    for system_dir in system_dirs:
        profile_path = system_dir / profile_type
        for file in profile_path.glob("*.json"):
            try:
                with open(file, "r") as f:
                    data = json.load(f)
                    if data.get("name") == inherit_name:
                        return data
            except Exception:
                continue
    return None

def flatten_profile(profile, profile_type):
    flattened = dict(profile)
    seen = set()
    inherit_name = flattened.get("inherits")

    while inherit_name and inherit_name not in seen:
        seen.add(inherit_name)
        inherited_profile = find_inherited_profile(profile_type, inherit_name)
        if not inherited_profile:
            break
        # Only add keys that don't exist in the child
        for key, value in inherited_profile.items():
            if key not in flattened:
                flattened[key] = value
        inherit_name = inherited_profile.get("inherits")

    # flattened.pop("inherits", None)
    return flattened

# Flatten all user profiles and save them to the flattened directory
for profile_type in ["filament", "machine", "process"]:
    for input_dir in input_dirs:
        input_dir = input_dir / profile_type
        output_dir = flattened_dir / profile_type

        for file in input_dir.glob("*.json"):
            try:
                with open(file, "r") as f:
                    profile = json.load(f)
                flattened = flatten_profile(profile, profile_type)
                output_file = output_dir / file.name
                with open(output_file, "w") as f:
                    json.dump(flattened, f, indent=2)
            except Exception as e:
                print(f"Error processing {file.name}: {e}")
