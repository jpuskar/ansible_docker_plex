# zfs-naskiller-config

# TODO
- drive power aggressiveness
- write caching?


## Adding a new drive

1. Create a key via 'dd bs=512 count=8 if=/dev/urandom of=/etc/.keys/bay0drive3.key'
2. chmod 400 /etc/.keys -R
3. Get drive serial number from the label.
4. Find current label by serial number (`hdparm -I /dev/sd?`).
5. partition
   fdisk /dev/thing
   (g, n, enter, enter, enter, p, w, enter)
6. cryptsetup luksFormat /dev/thing1
7. cryptsetup luksAddKey /dev/thing1 /etc/.keys/mydrive1.key
8. Get FS-UUID via udevadm info /dev/thing1
9. Add a Udev rule to a static mapping
   KERNEL=="sd?1", ENV{ID_FS_UUID}=="The-UUID-From-Before", SYMLINK+="mydrive1", RUN+="/usr/sbin/cryptsetup --key-file /etc/.keys/mydrive1.key luksOpen $env{DEVNAME} mydrive1_crypt"
10. udevadm control --reload


## Creating the zpool

zpool create -o ashift=12 tank0 mirror /dev/mapper/mydrive1_crypt /dev/mapper/mydrive2_crypt /dev/mapper/mydrive3_crypt
zfs create tank0/plex
zfs set quota=15T tank0/plex


## Replacing a drive

zpool replace tank0 old-disk-ref /dev/mapper/new-disk-ref


## clearing errors

zpool clear tank0 the-disk-ref


# Backups
1. udev rules for usb disks, and format them
2. zpool create -o ashift=12 backup-tank raidz /dev/mapper/zusb0_crypt /dev/mapper/zusb1_crypt /dev/mapper/zusb2_crypt /dev/mapper/zusb3_crypt
3. zfs create backup-tank/usb-backups

rsync -gloptrunci /tank0/backups/ /backup-tank/backups2/tank0/backups/
