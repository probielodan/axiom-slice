#!/bin/bash
# c++.sh — compile & run C++ like a script

if [ $# -lt 1 ]; then
  echo "Usage: $0 <file.cpp> [args...]"
  exit 1
fi

src="$1"
shift # remaining args passed to program

# base name without extension
base=$(basename "$src" .cpp)

# use mktemp for unique temporary binary
exe=$(mktemp --suffix=".${base}.out")

# compile with C++17 + warnings + optimizations
g++ -std=c++17 -Wall -Wextra -O2 "$src" -o "$exe"
status=$?

if [ $status -ne 0 ]; then
  echo "❌ Compilation failed"
  rm -f "$exe"
  exit $status
fi

# run program with user args
"$exe" "$@"

# clean up
rm -f "$exe"
