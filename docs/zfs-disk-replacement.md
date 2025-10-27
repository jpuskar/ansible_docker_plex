# zfs disk replacement

## new partition
parted /dev/thedisk -- mklabel gpt
parted /dev/thedisk -- mkpart primary 0% 100%
openssl rand -out /etc/.keys/bayXdriveYz.key 4096
chmod 0700 /etc/.keys/bayXdriveYz.key
cryptsetup luksFormat /dev/thedisk1 /etc/.keys/bayXdriveYz.key --sector-size=4096
cryptsetup luksOpen /dev/thedisk1 bayXdriveYz_crypt --key-file /etc/.keys/bayXdriveYz.key
blkid /dev/thedisk1  # Get the first UUID
vi /etc/udev/rules.d/92-drive-bays.rules   # copypasta one of the lines

zpool attach tank0 existingBayXdriveYa_crypt bayXdriveYz_crypt

# replaced 06/03/2025.
# KERNEL=="sd?1", ENV{ID_FS_UUID}=="the-uuid-here", SYMLINK+="bayXdriveYz", RUN+="/usr/sbin/cryptsetup --key-file /etc/.keys/bayXdriveYz.key luksOpen $env{DEVNAME} bayXdriveYz_crypt"

## hash all files
find /tank0/backups -type f -exec sha256sum '{}' \; >> /root/tank0-backups-06-04-2025.txt

## Check sector alignment

This should show '12' for 2^12 -> 4096
`zdb -C tank0 | grep ashift`

This should show 4096 if a 512e drive:
`blockdev --getss /dev/mapper/bay2drive2_crypt`

This should show the logical and physical size:
`smartctl -a /dev/sdb`


## Reformat disk for correct sector alignment

zpool tank0 detach bad-disk
wipefs -a /dev/bad-disk
cryptsetup luksFormat /dev/thedisk1 /etc/.keys/bayXdriveYz.key --sector-size=4096
cryptsetup luksOpen /dev/thedisk1 bayXdriveYz_crypt --key-file /etc/.keys/bayXdriveYz.key
blkid /dev/thedisk1  # Get the first UUID
vi /etc/udev/rules.d/92-drive-bays.rules   # copypasta one of the lines
zpool attach tank0 existingBayXdriveYa_crypt bayXdriveYz_crypt
