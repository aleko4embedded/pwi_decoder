#!/bin/bash
# Decode Pocket Word Ink (.pwi) files with pwi_decode.py.
# Text is saved as <file>.pwi.txt next to each file; notes with handwriting
# also get <file>.svg (written by pwi_decode.py).

# find pwi_decode.py next to this script, wherever it is started from
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DECODER="$SCRIPT_DIR/pwi_decode.py"

usage() {
	echo "Usage:"
	echo
	echo "$(basename "$0") [-d|--directory directory] [-f|--file file]"
	echo
	echo "  directory with pwi files -> all .pwi files in it will be processed"
	echo "  file pwi                 -> only this one will be processed"
	echo
}

if [ $# -eq 0 ]; then
	echo "missing arguments."
	usage
	exit 1
fi

if [ ! -f "$DECODER" ]; then
	echo "pwi_decode.py not found next to this script ($SCRIPT_DIR)."
	exit 1
fi

DIRECTORY=""
FILE=""
POSITIONAL_ARGS=()

while [[ $# -gt 0 ]]; do
  case $1 in
    -d|--directory)
      DIRECTORY="$2"
      shift # past argument
      shift # past value
      ;;
    -f|--file)
      FILE="$2"
      shift # past argument
      shift # past value
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    -*|--*)
      echo "Unknown option $1"
      usage
      exit 1
      ;;
    *)
      POSITIONAL_ARGS+=("$1") # save positional arg
      shift # past argument
      ;;
  esac
done

STATUS=0

decode() {
	echo "Processing: $1"
	if ! python3 "$DECODER" "$1" > "$1.txt"; then
		rm -f "$1.txt"   # don't leave an empty .txt behind on failure
		STATUS=1
	fi
}

if [ -n "$DIRECTORY" ]; then
	if [ -d "$DIRECTORY" ]; then
		shopt -s nullglob nocaseglob   # match .pwi and .PWI, skip if none
		FILES=("$DIRECTORY"/*.pwi)
		shopt -u nullglob nocaseglob
		if [ ${#FILES[@]} -eq 0 ]; then
			echo "No .pwi files in $DIRECTORY."
		fi
		for fl in "${FILES[@]}"; do
			[ -f "$fl" ] && decode "$fl"
		done
	else
		echo "$DIRECTORY does not exist."
		STATUS=1
	fi
fi

if [ -n "$FILE" ]; then
	if [ -f "$FILE" ]; then
		decode "$FILE"
	else
		echo "$FILE does not exist."
		STATUS=1
	fi
fi

exit $STATUS
