#!/bin/bash

# This script provides tab completion for the slice.sh script.
# To use it, save it to a file and source it in your shell configuration (e.g., ~/.bashrc).
#
# Example:
# source /path/to/slice_autocomplete.sh

_slice_completion() {
    # Get the current word the user is typing
    local cur=${COMP_WORDS[COMP_CWORD]}
    local prev=${COMP_WORDS[COMP_CWORD-1]}

    # Get the directory where the main script and configs are located
    # This assumes the autocomplete script is sourced from the same directory.
    # If not, you'll need to adjust this path.
    local script_dir=$(dirname "${BASH_SOURCE[0]}")
    local config_dir="$script_dir/OrcaSlicer/user/flattened"
    local filament_dir="$config_dir/filament"
    local machine_dir="$config_dir/machine"
    local process_dir="$config_dir/process"

    # Options to complete
    local opts="--debug --no-orient --filament --machine --process --output-name --dry-run --help"

    case "${prev}" in
        --filament)
            # Suggest filament config files
            # The -f flag suggests filenames, -P suggests a prefix
            COMPREPLY=( $(compgen -f -P "${filament_dir}/" -- "${cur}") )
            return 0
            ;;
        --machine)
            # Suggest machine config files
            COMPREPLY=( $(compgen -f -P "${machine_dir}/" -- "${cur}") )
            return 0
            ;;
        --process)
            # Suggest process config files
            COMPREPLY=( $(compgen -f -P "${process_dir}/" -- "${cur}") )
            return 0
            ;;
        --output-name|--debug)
            # No file or specific options to suggest, let bash do default filename completion
            return 0
            ;;
        *)
            # Default completion for flags and positional arguments
            # We add all known options first
            COMPREPLY=( $(compgen -W "${opts}" -- "${cur}") )

            # Then we add all files and directories
            # The -f flag suggests filenames
            # The -d flag suggests directory names
            COMPREPLY+=( $(compgen -f -d -- "${cur}") )

            # We filter out any options that have already been used
            for ((i=1; i < ${#COMP_WORDS[@]}-1; i++)); do
                COMPREPLY=( $(echo "${COMPREPLY[@]}" | tr ' ' '\n' | grep -v -x "${COMP_WORDS[i]}") )
            done
            return 0
            ;;
    esac
}

# Register the completion function for the 'slice.sh' command.
# Adjust 'slice.sh' if your script has a different name.
complete -F _slice_completion slice.sh
