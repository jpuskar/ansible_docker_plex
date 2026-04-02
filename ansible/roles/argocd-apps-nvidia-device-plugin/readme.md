
# Test with
```shell
KUBECONFIG=/home/${USER}/.kube/config \
    kubectl run nvidia-test \
        -n nvidia-device-plugin \
        --restart=Never \
        -ti --rm \
        --image nvcr.io/nvidia/cuda:13.1.0-runtime-ubuntu24.04 \
        --overrides '{"spec": {"runtimeClassName": "nvidia"}}' \
            -- nvidia-smi
```


```shell
Sun Jan 11 00:47:10 2026       
+-----------------------------------------------------------------------------------------+
| NVIDIA-SMI 580.105.08             Driver Version: 580.105.08     CUDA Version: 13.0     |
+-----------------------------------------+------------------------+----------------------+
| GPU  Name                 Persistence-M | Bus-Id          Disp.A | Volatile Uncorr. ECC |
| Fan  Temp   Perf          Pwr:Usage/Cap |           Memory-Usage | GPU-Util  Compute M. |
|                                         |                        |               MIG M. |
|=========================================+========================+======================|
|   0  NVIDIA GB10                    On  |   0000000F:01:00.0 Off |                  N/A |
| N/A   39C    P8              4W /  N/A  | Not Supported          |      0%      Default |
|                                         |                        |                  N/A |
+-----------------------------------------+------------------------+----------------------+

+-----------------------------------------------------------------------------------------+
| Processes:                                                                              |
|  GPU   GI   CI              PID   Type   Process name                        GPU Memory |
|        ID   ID                                                               Usage      |
|=========================================================================================|
|  No running processes found                                                             |
+-----------------------------------------------------------------------------------------+
pod "nvidia-test" deleted from nvidia-device-plugin namespace
```

# Another!
https://www.siderolabs.com/blog/ai-workloads-on-talos-linux/
https://docs.siderolabs.com/talos/v1.12/configure-your-talos-cluster/hardware-and-drivers/nvidia-gpu?search=+1#testing-the-runtime-class

```shell
# Create the pod with sleep infinity and nvidia runtime
kubectl run "node-debugger-${USER}" \
  --restart=Never \
  --namespace kube-system \
  --image nvcr.io/nvidia/cuda:13.1.0-devel-ubuntu24.04 \
  --overrides '{"spec": {"runtimeClassName": "nvidia"}}' \
  -- sleep infinity

# Then exec into it
kubectl exec -it "node-debugger-${USER}" \
  --namespace kube-system \
  -- /bin/bash
```

```shell
# Create the pod with sleep infinity and nvidia runtime
kubectl run "node-debugger-${USER}-vectoradd" \
  --restart=Never \
  --namespace kube-system \
  --image nvcr.io/nvidia/k8s/cuda-sample:vectoradd-cuda12.5.0-ubi8 \
  --overrides '{"spec": {"runtimeClassName": "nvidia"}}' \
  -- sleep infinity

# Then exec into it
kubectl exec -it "node-debugger-${USER}" \
  --namespace kube-system \
  -- /bin/bash
```

```yaml
apiVersion: v1
kind: Pod
metadata:
  name: node-debugger-user-vectoradd
  namespace: kube-system
spec:
  runtimeClassName: nvidia
  restartPolicy: Never
  containers:
    - name: vectoradd
      image: nvcr.io/nvidia/k8s/cuda-sample:vectoradd-cuda12.5.0-ubi8
      command:
        - sleep
        - infinity
```


```yaml
apiVersion: v1
kind: Pod
metadata:
  name: cuda-devicequery-cuda11-7-1
  namespace: kube-system
spec:
  restartPolicy: Never
  containers:
  - name: test
    image: nvcr.io/nvidia/k8s/cuda-sample:devicequery-cuda11.7.1-ubuntu20.04
    resources:
      limits:
        nvidia.com/gpu: 1
```


# Building devicequery

```yaml
apiVersion: v1
kind: Pod
metadata:
  name: cuda13-devicequery-build
  namespace: kube-system
spec:
  restartPolicy: Never
  runtimeClassName: nvidia
  containers:
  - name: cuda
    image: nvcr.io/nvidia/cuda:13.0.0-devel-ubuntu24.04
    resources:
      limits:
        nvidia.com/gpu: 1
    command:
      - sleep
      - infinity
#    command: ["bash","-lc"]
#    args:
#      - |
#        set -e
#        apt-get update
#        apt-get install -y git build-essential
#        git clone --depth=1 https://github.com/NVIDIA/cuda-samples.git
#        cd cuda-samples/Samples/1_Utilities/deviceQuery
#        make
#        ./deviceQuery
```

then run:
```shell
apt-get update
apt-get install -y git build-essential cmake
git clone --depth=1 https://github.com/NVIDIA/cuda-samples.git
cd cuda-samples
mkdir build && cd build
cmake ..
cmake --build . -j
./Samples/1_Utilities/deviceQuery/deviceQuery
```

ldconfig -p | grep -E 'libcuda\.so|libnvidia-ml\.so'
ls -l /usr/lib*/libcuda.so* /usr/local/glibc/usr/lib/libcuda.so* 2>/dev/null || true

ls -l /usr/lib*/libcuda.so* /usr/local/glibc/usr/lib/libcuda.so* 2>/dev/null || true
ldconfig -p | grep libcuda || true

nvidia-smi -q

...
GPU Operation Mode
    Current : N/A
    Pending : N/A
...
Compute Mode : Default
...

python3 - <<'PY'
import os
paths=[
  "/dev/nvidiactl",
  "/dev/nvidia0",
  "/dev/nvidia-uvm",
  "/dev/nvidia-uvm-tools",
  "/dev/nvidia-modeset",
  "/dev/nvidia-caps/nvidia-cap1",
  "/dev/nvidia-caps/nvidia-cap2",
]
for p in paths:
  try:
    fd=os.open(p, os.O_RDWR)
    os.close(fd)
    print("OK", p)
  except Exception as e:
    print("FAIL", p, e)
PY

OK /dev/nvidiactl
OK /dev/nvidia0
OK /dev/nvidia-uvm
OK /dev/nvidia-uvm-tools
OK /dev/nvidia-modeset
FAIL /dev/nvidia-caps/nvidia-cap1 [Errno 1] Operation not permitted: '/dev/nvidia-caps/nvidia-cap1'
FAIL /dev/nvidia-caps/nvidia-cap2 [Errno 1] Operation not permitted: '/dev/nvidia-caps/nvidia-cap2'


apiVersion: v1
kind: Pod
metadata:
  name: cuda13-devicequery-build-caps
  namespace: kube-system
spec:
  restartPolicy: Never
  runtimeClassName: nvidia
  containers:
  - name: cuda
    image: nvcr.io/nvidia/cuda:13.0.0-devel-ubuntu24.04 
    securityContext:
      privileged: true
      allowPrivilegeEscalation: true
      seccompProfile:
        type: Unconfined
      capabilities:
        add: ["SYS_ADMIN", "SYS_PTRACE"]
    resources:
      limits:
        nvidia.com/gpu: 1
    command:
      - sleep
      - infinity
