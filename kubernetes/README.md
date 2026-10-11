# Single node kubernetes cluster

###### Initialize single node kubernetes cluster

```
kubeadm init --pod-network-cidr=10.244.0.0/16 --control-plane-endpoint=kubeadm.feit.ukim.edu.mk --cri-socket unix:///run/docker.sock
```

```
kubeadm init --pod-network-cidr=10.244.0.0/16 --control-plane-endpoint=kubeadm.feit.ukim.edu.mk --cri-socket unix:///run/containerd/containerd.sock
```

```
kubeadm init --pod-network-cidr=10.244.0.0/16 --control-plane-endpoint=kubeadm.feit.ukim.edu.mk --cri-socket unix:///run/cri-dockerd.sock
```

###### Taint the master node with a control plane to deploy pods

```
kubectl taint nodes --all node-role.kubernetes.io/control-plane:NoSchedule-
```

###### Apply flannel network for kubernetes pods

```
kubectl apply -f https://github.com/flannel-io/flannel/releases/latest/download/kube-flannel.yml
```

###### Apply metrics server for cluster

```
helm repo add metrics-server https://kubernetes-sigs.github.io/metrics-server/
helm repo update
```

```
helm upgrade --install metrics-server metrics-server/metrics-server -n kube-system --create-namespace --values \
kubernetes/metrics-server/values.yml
```

###### Apply cert-manager resources

```
kubectl apply -f https://github.com/jetstack/cert-manager/releases/latest/download/cert-manager.yaml
```

###### Apply ingress-nginx resources with values from a custom yml file

```
helm repo add ingress-nginx https://kubernetes.github.io/ingress-nginx
helm repo update
```

```
helm upgrade --install ingress-nginx ingress-nginx/ingress-nginx -n ingress-nginx --create-namespace --values \
kubernetes/ingress/ingress-nginx-values.yml
```

###### Apply MetalLB resources for deploying a load balancer

```
kubectl apply -f kubernetes/metallb/ip-address-pool.yml
kubectl apply -f kubernetes/metallb/l2-advertisement.yml
kubectl apply -f kubernetes/metallb/metallb-native.yml
```

###### Apply sealed secrets controller and generate sealed secrets from existing secrets

```
kubectl apply -f https://github.com/bitnami-labs/sealed-secrets/releases/download/v0.34.0/controller.yaml
```

```
kubeseal < kubernetes/secret/flask-secret.yml -o yaml > kubernetes/sealed-secret/flask-sealed-secret.yml
kubeseal < kubernetes/secret/mongo-secret.yml -o yaml > kubernetes/sealed-secret/mongo-sealed-secret.yml
```

```
kubectl apply -f kubernetes/sealed-secret/flask-sealed-secret.yml
kubectl apply -f kubernetes/sealed-secret/mongo-sealed-secret.yml
```

###### Apply all system resources

```
kubectl apply -k kubernetes
kubectl apply -f kubernetes/deployment/flask-deployment.yml
kubectl apply -f kubernetes/deployment/mongo-deployment.yml
kubectl apply -f kubernetes/deployment/redis-deployment.yml
kubectl apply -f kubernetes/deployment/redis-queue-deployment.yml
kubectl apply -f kubernetes/deployment/worker-deployment.yml
```

###### The worker

`worker` runs the background jobs: data fetching, model training, forecasting and the data
backup. It runs two containers from the API image. `worker` drains the RQ queues and `cron`
enqueues jobs on schedule. The queue lives in `redis-queue`, a second Redis with `noeviction` and
an append-only file on `/mnt/aqra/queue`, kept apart from the cache Redis, which evicts keys.

`flask` and `worker` mount the same `flask-data-pvc`. It is a `ReadWriteOnce` hostPath volume,
so both pods must run on the same node. That holds on this single-node cluster; a second node
would need a `ReadWriteMany` volume or a node affinity rule first.

```
kubectl logs -n aqra deploy/worker -c worker -f
kubectl exec -n aqra deploy/worker -c worker -- rq info --url redis://redis-queue:6379/0
kubectl exec -n aqra deploy/worker -c worker -- python3 -m worker bootstrap   # enqueue a job by hand
```

###### Readings in MongoDB

The database holds one reading per sensor and hour. Run the migration once when the cluster is
back: it reports first, then pushes any reading only the CSV cache holds, removes duplicate
readings older code could insert, and creates the unique `(sensorId, time)` index. A second run
changes nothing.

```
kubectl exec -n aqra deploy/worker -c worker -- python3 -m worker.migrate --dry-run
kubectl exec -n aqra deploy/worker -c worker -- python3 -m worker.migrate
```

The nightly `flask-restart` CronJob is gone: it restarted the API to release memory the in-process
training held. `kubectl apply -k` does not prune, so delete it once by hand:

```
kubectl delete cronjob flask-restart -n aqra --ignore-not-found
kubectl delete rolebinding flask-restart-rolebinding -n aqra --ignore-not-found
kubectl delete role flask-restart-role -n aqra --ignore-not-found
kubectl delete serviceaccount flask-restart-sa -n aqra --ignore-not-found
```

###### Get deployed pods in namespace

```
kubectl get pods -n aqra
```

###### Describe deployed pods

```
kubectl describe pod [pod-name] -n aqra
```

```
kubectl get pods -n aqra | grep -E 'flask[a-z0-9\-]*' -iwo | tr -d '\n' | xargs kubectl describe pod -n aqra
```

```
kubectl get pods -n aqra | grep -E 'mongo[a-z0-9\-]*' -iwo | tr -d '\n' | xargs kubectl describe pod -n aqra
```

###### Follow logs of the deployed pod

```
kubectl logs -f [pod-name] -n aqra
```

```
kubectl get pods -n aqra | grep -E 'flask[a-z0-9\-]*' -iwo | tr -d '\n' | xargs kubectl logs -f -n aqra
```

```
kubectl get pods -n aqra | grep -E 'mongo[a-z0-9\-]*' -iwo | tr -d '\n' | xargs kubectl logs -f -n aqra
```

###### Enter bash of the deployed pod

```
kubectl exec -n aqra --stdin --tty [pod-name] -- /bin/bash
```

```
kubectl exec -n aqra --stdin --tty $(kubectl get pods -n aqra | grep -E 'flask[a-z0-9\-]*' -iwo | tr -d '\n') -- /bin/bash
```

```
kubectl exec -n aqra --stdin --tty $(kubectl get pods -n aqra | grep -E 'mongo[a-z0-9\-]*' -iwo | tr -d '\n') -- /bin/bash
```

##### Describe deployed pods

```
kubectl describe pod -f [pod-name] -n aqra
```

```
kubectl get pods -n aqra | grep -E 'flask[a-z0-9\-]*' -iwo | tr -d '\n' | xargs describe pod -n aqra
```

```
kubectl get pods -n aqra | grep -E 'mongo[a-z0-9\-]*' -iwo | tr -d '\n' | xargs describe pod -n aqra
```

##### Resource usage of node and deployed pods

```
kubectl top nodes --show-capacity
```

```
kubectl top pods --containers --sum -n aqra
```

###### Delete and reapply deployments if changes are made to the Docker images

```
kubectl delete -f kubernetes/deployment/flask-deployment.yml
kubectl delete -f kubernetes/deployment/mongo-deployment.yml
```

```
kubectl apply -f kubernetes/deployment/flask-deployment.yml
kubectl apply -f kubernetes/deployment/mongo-deployment.yml
```

###### Retrieve sealed secrets from the cluster

```
kubectl get secret -n kube-system -l sealedsecrets.bitnami.com/sealed-secrets-key -o yaml > kubernetes/master.key

kubeseal --recovery-unseal < kubernetes/sealed-secret/flask-sealed-secret.yml --recovery-private-key kubernetes/master.key -o yaml > \
kubernetes/secret/flask-secret.yml
kubeseal --recovery-unseal < kubernetes/sealed-secret/mongo-sealed-secret.yml --recovery-private-key kubernetes/master.key -o yaml > \
kubernetes/secret/mongo-secret.yml
```

###### Cleanup resources by deleting persistent volumes and used namespaces

```
kubectl delete namespace aqra
kubectl delete namespace cert-manager
kubectl delete namespace ingress-nginx
kubectl delete namespace metallb-system
kubectl delete pv flask-data-pv mongo-data-pv
```

###### Reset kubernetes cluster

```
kubeadm reset
```

###### Upgrade the kubernetes cluster

```
kubeadm upgrade plan
kubeadm upgrade apply latest
```