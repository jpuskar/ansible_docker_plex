
# On GB10

1. Get RDP working/
   2. Enable in settings
   3. in mremoteng make sure to choose a specific resolution. The ubuntu machine doesn't like smartsize or fit-to-panel.
2. Document OOB versions and nvidia-smi
3. snap install microk8s
4. install nvidia device plugin


## OOB
nvidia-smi
```shell
+-----------------------------------------------------------------------------------------+
| NVIDIA-SMI 580.95.05              Driver Version: 580.95.05      CUDA Version: 13.0     |
+-----------------------------------------+------------------------+----------------------+
| GPU  Name                 Persistence-M | Bus-Id          Disp.A | Volatile Uncorr. ECC |
| Fan  Temp   Perf          Pwr:Usage/Cap |           Memory-Usage | GPU-Util  Compute M. |
|                                         |                        |               MIG M. |
|=========================================+========================+======================|
|   0  NVIDIA GB10                    On  |   0000000F:01:00.0  On |                  N/A |
| N/A   42C    P0             11W /  N/A  | Not Supported          |      0%      Default |
|                                         |                        |                  N/A |
+-----------------------------------------+------------------------+----------------------+

+-----------------------------------------------------------------------------------------+
| Processes:                                                                              |
|  GPU   GI   CI              PID   Type   Process name                        GPU Memory |
|        ID   ID                                                               Usage      |
|=========================================================================================|
|    0   N/A  N/A          155814      G   /usr/lib/xorg/Xorg                       43MiB |
|    0   N/A  N/A          156040      G   /usr/bin/gnome-shell                     14MiB |
|    0   N/A  N/A          158058    C+G   ...c/gnome-remote-desktop-daemon        176MiB |
|    0   N/A  N/A          158148      G   /usr/bin/gnome-shell                    125MiB |
|    0   N/A  N/A          158441    C+G   ...c/gnome-remote-desktop-daemon        258MiB |
|    0   N/A  N/A          158502      G   /usr/bin/Xwayland                         8MiB |
+-----------------------------------------------------------------------------------------+
```

```shell
snap install microk8s
mkdir -p ~/.kube
microk8s config ~/.kube/config
```

Then because it installs an old version by default, and figuring that out is nonintuitive with snaps:
```shell
snap info microk8s | grep -i \/stable | sort | tail
# look through the list for a while
snap refresh microk8s --channel=1.35/stable
```

NOTE: enable GPU won't work. the plugin is not officially supported on ARM.
But instead of telling you this, Canonical makes you guess with a misleading 'not found in repo' error.

Ref: https://forums.developer.nvidia.com/t/microk8s-on-spark/348506/3
Reads as follows (*for reference only. see specific instructions below)*:
> I was able to get this working, though the gpu addon is not enabled on arm64 by default “because the gpu addon is only tested on amd64 for new releases” as per MicroK8s contributor Angelos Kolaitis. (see Cannot enable gpu addon on aarch64 instances (aws g5g.metal) · Issue #4454 · canonical/microk8s · GitHub )
> 
> Procedure followed:
> 
> 1. Install microk8s via sudo snap install microk8s --classic
> 2. Add arm64 to the supported_architectures of the nvidia and gpu addons in /var/snap/microk8s/common/addons/core/addons.yaml
> 3. Enable the gpu addon via sudo microk8s enable gpu
> 4. Deploy the NVIDIA device plugin for ARM64 manually by running sudo microk8s kubectl apply -f https://raw.githubusercontent.com/NVIDIA/k8s-device-plugin/refs/tags/v0.18.0/deployments/static/nvidia-device-plugin.yml
> 5. Update the cuda-vector-add container image in the “MicroK8s on NVIDIA DGX” example to nvcr.io/nvidia/k8s/cuda-sample:vectoradd-cuda12.5.0-ubuntu22.04
> After running sudo microk8s kubectl apply on the updated cuda-vector-add manifest, the pod was scheduled and deployed:
> 
> $ sudo microk8s kubectl logs cuda-vector-add
> [Vector addition of 50000 elements]
> Copy input data from the host memory to the CUDA device
> CUDA kernel launch with 196 blocks of 256 threads
> Copy output data from the CUDA device to the host memory
> Test PASSED
> Done

```shell
root@promaxgb10-bda9:/home/user# vi /var/snap/microk8s/common/addons/core/addons.yaml 
root@promaxgb10-bda9:/home/user# microk8s enable gpu
Infer repository core for addon gpu

WARNING: The gpu addon has been renamed to nvidia.

Please use 'microk8s enable nvidia' instead.


Addon core/dns is already enabled
Addon core/helm3 is already enabled
Checking if NVIDIA driver is already installed
GPU 0: NVIDIA GB10 (UUID: GPU-e793e0a4-fd41-dcd8-a833-3127370a6779)
"nvidia" has been added to your repositories
Hang tight while we grab the latest from your chart repositories...
...Successfully got an update from the "nvidia" chart repository
Update Complete. ⎈Happy Helming!⎈
Deploy NVIDIA GPU operator
Using host GPU driver
I0111 12:46:41.342844   26441 warnings.go:110] "Warning: spec.template.spec.affinity.nodeAffinity.preferredDuringSchedulingIgnoredDuringExecution[0].preference.matchExpressions[0].key: node-role.kubernetes.io/master is use \"node-role.kubernetes.io/control-plane\" instead"
I0111 12:46:41.344395   26441 warnings.go:110] "Warning: spec.template.spec.affinity.nodeAffinity.preferredDuringSchedulingIgnoredDuringExecution[0].preference.matchExpressions[0].key: node-role.kubernetes.io/master is use \"node-role.kubernetes.io/control-plane\" instead"
NAME: gpu-operator
LAST DEPLOYED: Sun Jan 11 12:46:41 2026
NAMESPACE: gpu-operator-resources
STATUS: deployed
REVISION: 1
TEST SUITE: None
Deployed NVIDIA GPU operator
```

https://canonical.com/microk8s/docs/nvidia-dgx
```yaml
apiVersion: v1
kind: Pod
metadata:
  name: cuda-vector-add
spec:
  restartPolicy: OnFailure
  containers:
    - name: cuda-vector-add
      image: "nvcr.io/nvidia/k8s/cuda-sample:vectoradd-cuda12.5.0-ubuntu22.04"
      resources:
        limits:
          nvidia.com/gpu: 1
```
logs:
```shell
[Vector addition of 50000 elements]
Copy input data from the host memory to the CUDA device
CUDA kernel launch with 196 blocks of 256 threads
Copy output data from the CUDA device to the host memory
Test PASSED
Done
```


This fails with:
```shell
2026-01-11 19:12:16.627 ERROR [28:28] dcgmWatchFields() returned -33. [/builds/dcgm/dcgm/dcgmproftester/DcgmProfTester.cpp:231] [DcgmProfTester::WatchFields]
2026-01-11 19:12:16.827 ERROR [28:28] Error -33 from RunTests(). Exiting. [/builds/dcgm/dcgm/dcgmproftester/DcgmProfTester.cpp:1263] [main]
```

```yaml
apiVersion: v1
kind: Pod
metadata:
  name: dcgm-gpu-stress
spec:
  restartPolicy: Never
  runtimeClassName: nvidia
  containers:
    - name: dcgm
      image: nvidia/dcgm:4.4.2-1-ubuntu22.04
      command: ["/bin/sh", "-c"]
      args:
        - |
          ls -lah /usr/bin/*;
          /usr/bin/dcgmproftester13 -t 1004 -d 0
 ```


This don't work either:
```yaml
apiVersion: v1
kind: Pod
metadata:
  name: dcgm-diag
spec:
  runtimeClassName: nvidia
  restartPolicy: Never
  containers:
    - name: dcgm
      image: nvidia/dcgm:4.4.2-1-ubuntu22.04
      command: ["/bin/sh","-c"]
      args:
        - dcgmi diag -r 3
      resources:
        limits:
          nvidia.com/gpu: 1
```
```shell
| Diagnostic                | Result                                         |
+===========================+================================================+
|-----  Metadata  ----------+------------------------------------------------|
| DCGM Version              | 4.4.2                                          |
| Driver Version Detected   | 580.95.05                                      |
| GPU Device IDs Detected   | 2e12                                           |
|-----  Deployment  --------+------------------------------------------------|
| software                  | Pass                                           |
|                           | GPU0: Pass                                     |
+-----  Hardware  ----------+------------------------------------------------+
| memory                    | Skip                                           |
|                           | GPU0: Skip                                     |
| diagnostic                | Skip                                           |
|                           | GPU0: Skip                                     |
| nvbandwidth               | Skip                                           |
|                           | GPU0: Skip                                     |
+-----  Integration  -------+------------------------------------------------+
| pcie                      | Skip                                           |
|                           | GPU0: Skip                                     |
+-----  Stress  ------------+------------------------------------------------+
| memory_bandwidth          | Skip                                           |
|                           | GPU0: Skip                                     |
| targeted_stress           | Skip                                           |
|                           | GPU0: Skip                                     |
| targeted_power            | Skip                                           |
|                           | GPU0: Skip                                     |
+---------------------------+------------------------------------------------+
```

Fails /w bad arch
```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: cuda-nbody
spec:
  replicas: 2
  selector:
    matchLabels:
      app: cuda-nbody
  template:
    metadata:
      labels:
        app: cuda-nbody
    spec:
      containers:
        - name: cuda-nbody
          image: nvcr.io/nvidia/k8s/cuda-sample:nbody-cuda11.7.1-ubuntu18.04
          command: ["/bin/bash", "-c"]
          args:
            - /cuda-samples/nbody -benchmark -numbodies=65536
          # resources:
            # limits:
              # nvidia.com/gpu: 1
```

Fails with errors:
```shell
Traceback (most recent call last):
  File "<string>", line 2, in <module>
  File "/usr/local/lib/python3.10/dist-packages/torch/cuda/__init__.py", line 302, in _lazy_init
    torch._C._cuda_init()
RuntimeError: Found no NVIDIA driver on your system. Please check that you have an NVIDIA GPU and installed a driver from http://www.nvidia.com/Download/index.aspx
```
```yaml
apiVersion: v1
kind: Pod
metadata:
  name: l4t-pytorch-burn
spec:
  runtimeClassName: nvidia
  restartPolicy: Never
  containers:
    - name: torch
      image: dustynv/l4t-pytorch:r36.2.0
      command: ["python3", "-c"]
      args:
        - |
          import torch
          a = torch.randn((8192,8192), device="cuda")
          b = torch.randn((8192,8192), device="cuda")
          while True:
              (a @ b).sum().item()
              torch.cuda.synchronize()
```

BUT THIS WORKS!!
```yaml
apiVersion: v1
kind: Pod
metadata:
  name: l4t-pytorch-burn3
spec:
  runtimeClassName: nvidia
  restartPolicy: Never
  containers:
    - name: torch
      image: nvcr.io/nvidia/pytorch:25.12-py3
      command: ["python3", "-c"]
      args:
        - |
          import torch
          a = torch.randn((8192,8192), device="cuda")
          b = torch.randn((8192,8192), device="cuda")
          while True:
              (a @ b).sum().item()
              torch.cuda.synchronize()
```


---
stop here
---


Create runtime class:
```yaml
apiVersion: node.k8s.io/v1
kind: RuntimeClass
metadata:
  name: nvidia
handler: nvidia
```



https://docs.nvidia.com/datacenter/cloud-native/gpu-operator/latest/getting-started.html#microk8s
values file:
```yaml
image:
  tag: "v0.18.1"

# Runtime class for NVIDIA GPU workloads
runtimeClassName: nvidia

# gpu-feature-discovery
gfd:
  enabled: true

# node-feature-discovery
nfd:
  nameOverride: node-feature-discovery
  enableNodeFeatureApi: true

# GPUDirect Storage
gdsEnabled: true
```

```shell
helm repo add nvidia-device-plugin https://nvidia.github.io/k8s-device-plugin
helm repo update nvidia-device-plugin
helm template nvidia-device-plugin nvidia-device-plugin/nvidia-device-plugin \
    --version 0.18.0 \
    --namespace nvidia-device-plugin \
    --values ./ndp-values.yaml \
    --include-crds
```
