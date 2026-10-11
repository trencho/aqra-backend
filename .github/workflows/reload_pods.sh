#!/bin/bash
set -e  # Stop on error

kubectl rollout restart deployment mongo -n aqra
kubectl rollout restart deployment flask -n aqra
kubectl rollout restart deployment worker -n aqra

echo "Waiting for deployments to be ready..."
kubectl rollout status deployment/mongo -n aqra --watch=true
kubectl rollout status deployment/flask -n aqra --watch=true
kubectl rollout status deployment/worker -n aqra --watch=true

# The host-wide `docker system prune -a --volumes --force` that used to run here
# was removed: it is not scoped to this project, so it deleted other workloads'
# images and volumes on every deploy. Nothing is built on the node any more, so
# there is nothing here to clean up. aqra-frontend PR #2 removed the same line.
