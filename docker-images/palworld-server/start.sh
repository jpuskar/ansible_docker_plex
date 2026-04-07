#!/bin/bash
set -euo pipefail

# Ref: https://gist.github.com/Bluefissure/b0fcb05c024ee60cad4e23eb55463062

ls -lah /server
ls -lah /server/linux64

#echo "------------------------------------------------"
#cat /server/DefaultPalWorldSettings.ini
#echo "------------------------------------------------"

if [[ ! -f /server/Pal/Saved/Config/LinuxServer/PalWorldSettings.ini ]]; then
  cd /server/Pal/Saved && tar -xvf /default_config.tar.gz
fi
ls -lah /server/Pal/Saved


python3 /configure.py --secret-namespace palworld0

echo "++++++++++++++++++++++++++++++++++++++++++++++++"
cat /server/Pal/Saved/Config/LinuxServer/PalWorldSettings.ini
echo "++++++++++++++++++++++++++++++++++++++++++++++++"

cd /server
HOME=/home/steam /server/PalServer.sh -useperfthreads -NoAsyncLoadingThread -UseMultithreadForDS

# TODO: RCON
# TODO: Trap and run save before exiting.
# TODO: log level
# TODO: reboot on ram threshold
# TODO: save / backups
