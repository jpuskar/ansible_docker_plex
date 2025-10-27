# find /tank0/backups/ -type f -exec sha256sum '{}' \; >> /root/left-zpool-backups.txt

with open('/root/left-zpool-tank0_backups.txt', 'r', encoding='cp1252') as f:
    left = f.readlines()

# with open('/root/backups-unknown.txt', 'r', encoding='cp1252') as f:
#     left2 = f.readlines()
#
# left_all = left + left2

zpool_rel_map = {}
for line in left:
    _hash = line[0:64]
    _path = line[66:]
    zpool_rel_map[_hash] = _path

# find /mnt/9QG6V2MJ/ -type f -exec sha256sum '{}' \; >> /root/USB.txt
# find /mnt/tmp2/ -type f -exec sha256sum '{}' \; >> /root/right-C4BEF2A1-27E8-4EA4-9547-BA3C1D74C70A.txt
with open('/root/right-zpool-tank0_staged_backup.txt', 'r', encoding='cp1252') as f:
    right = f.readlines()

usb_rel_map = {}
for line in right:
    _hash = line[0:64]
    _path = line[66:]
    usb_rel_map[_hash] = _path

files_missing_from_zpool = [
    x for x
    in usb_rel_map.items()
    if x[0] not in zpool_rel_map.keys()
]
print('files missing from zpool:')
for _record in files_missing_from_zpool:
    print(_record)

extra_files_on_zpool_not_on_usb = [
    x for x
    in zpool_rel_map.items()
    if x[0] not in usb_rel_map.keys()
]
# print('extra files on zpool that are not on the usb:')
# for _record in extra_files_on_zpool_not_on_usb:
#     print(_record)

# TODO: filenames where hashes don't match
