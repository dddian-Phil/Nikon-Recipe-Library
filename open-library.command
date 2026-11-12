#!/bin/bash
exec /bin/bash "$(dirname -- "$0")/app/run-macos.sh" open "$@"
