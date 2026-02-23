# ELEC490 - Capstone - Cluster Computing Project

---
### Cluster Info
6 Alienware PCs  
Nvidia GeForce RTX 3060 8GB  
OS: Ubuntu 24.04.2 LTS  

---
### How to Access Cluster (Head Node)
1. Install TailScale: https://tailscale.com/
2. Login through cluster Google account
3. Connect your device to the cluster
4. Open terminal and use `ssh cluster@group15cluster-2`

luster Setup Steps
(First two steps to undo incorrect setup)
**Check**
`nmcli con show`
**Delete**
`sudo nmcli con delete "Wired connection 1"
**Create**
`sudo nano /etc/netplan/01-cluster-eno1.yaml`

**Paste in:**
```
network:
  version: 2
  ethernets:
    eno1:
      dhcp4: no
      addresses:
        - 192.168.50.X/24
```

**Save then Apply**
`sudo netplan apply`

**Verify**
`ip a show eno1
`ip route`

**Expect**
`inet 192.168.50.1/24
`192.168.50.0/24 dev eno1`

**Set hostnames**
`sudo nano /etc/hosts

**Paste in:**
```
192.168.50.1 headnode
192.168.50.2 node2
192.168.50.3 node3
192.168.50.4 node4
192.168.50.5 node5
192.168.50.6 node6
```

**Now from headnode**
`ssh nodeX`
Can access over ethernet NOT TailScale

---
### NVIDIA Driver + CUDA install

**Purge existing state**
sudo apt purge -y 'nvidia*'
sudo apt purge -y 'libnvidia*'
sudo apt purge -y 'cuda*'
sudo apt autoremove -y
sudo apt autoclean
sudo reboot
`

**Add NVIDIA CUDA Repository**
sudo apt update
sudo apt install -y wget gnupg`

wget https://developer.download.nvidia.com/compute/cuda/repos/ubuntu2404/x86_64/cuda-keyring_1.1-1_all.deb
sudo dpkg -i cuda-keyring_1.1-1_all.deb
sudo apt update

**Install exact driver**
apt-cache search nvidia-driver-580
sudo apt install -y nvidia-driver-580
sudo reboot

**Verify**
nvidia-smi

---
### IP Forwarding
#### Headnode
Enable IP Forwarding
sudo sysctl -w net.ipv4.ip_forward=1
echo "net.ipv4.ip_forward=1" | sudo tee /etc/sysctl.d/99-cluster-forwarding.conf

Enable NAT
ip route | grep default
sudo iptables -t nat -A POSTROUTING -s 192.168.50.0/24 -o enx9c69d3273282 -j MASQUERADE

Persist Rules
sudo apt install -y iptables-persistent
sudo netfilter-persistent save

#### Compute Node
**Add default route**
sudo ip route add default via 192.168.50.1
ip route

**Add DNS servers**
sudo nano /etc/systemd/resolved.conf

Paste in:
[Resolve]
DNS=8.8.8.8 1.1.1.1
FallbackDNS=9.9.9.9

**Restart Resolver**
sudo systemctl restart systemd-resolved

**Test**
ping -c 3 8.8.8.8
ping -c 3 google.com

**Make Routing Persistant**
Add to Netplan
sudo nano /etc/netplan/01-cluster-eno1.yaml

network:
  version: 2
  ethernets:
    eno1:
      dhcp4: no
      addresses:
        - 192.168.50.X/24
      routes:
        - to: default
          via: 192.168.50.1
      nameservers:
        addresses:
          - 8.8.8.8
          - 1.1.1.1


sudo netplan apply

**Disabling Wifi Route**
sudo nmcli device set wlp5s0 managed no
sudo ip link set wlp5s0 down

Disabling Wifi Route Reboot Reset
sudo nano /etc/NetworkManager/conf.d/99-disable-wifi.conf

paste:
[keyfile]
unmanaged-devices=interface-name:wlp5s0

sudo systemctl restart NetworkManager
