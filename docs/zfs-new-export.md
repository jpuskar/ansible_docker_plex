
sudo zfs create tank/media
sudo zfs set mountpoint=/srv/media tank/media
sudo zfs set quota=2T tank/media
zfs list -o name,used,avail,mountpoint,quota tank/media
sudo zfs set sharenfs="rw=10.0.x.y:10.0.x.y:10.0.x.y:10.0.x.y:10.0.x.y:10.0.x.y,sync,no_subtree_check" tank/apps

sudo ufw allow from 192.168.x.y/32 to any port 2049 proto tcp
sudo ufw allow from 192.168.x.y/32 to any port 2049 proto udp
