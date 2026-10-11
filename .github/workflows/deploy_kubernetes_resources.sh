#!/bin/bash
set -e  # Stop on error

KUBERNETES_DIR="${GIT_REPO_PATH}/kubernetes"

# Preflight: fail fast (and clearly) if the cluster is unreachable, instead of a cryptic
# "connection to the server ... was refused" partway through the applies.
echo "Preflight: verifying the Kubernetes API server is reachable..."
if ! kubectl cluster-info --request-timeout=15s >/dev/null 2>&1; then
  echo "ERROR: Kubernetes API server is unreachable - aborting before any 'kubectl apply'."
  echo "Details:"
  kubectl cluster-info --request-timeout=15s 2>&1 | sed 's/^/  /' || true
  echo "The control plane is likely down, or the kubeconfig points at the wrong host/port."
  echo "On the server, check:  systemctl status kubelet  ;  kubectl cluster-info  ;  that kube-apiserver is listening on :6443."
  exit 1
fi
echo "Kubernetes API server reachable - proceeding with the deploy."

# Apply a Deployment and wait for it, without tripping over stale status.
#
# `kubectl rollout status` reads .status.conditions, which still describe the PREVIOUS
# rollout until the deployment controller has observed this apply. Run back-to-back with
# `kubectl apply`, it therefore reports the OLD result: flask's deploy failed twice with
# "exceeded its progress deadline" emitted ~100ms after the apply, long before the new
# pod had done anything at all. Raising progressDeadlineSeconds does not help, because
# the stale condition is not recomputed until a new rollout is under way.
#
# So: wait for .status.observedGeneration to catch up to .metadata.generation first.
# Only then does rollout status describe THIS rollout.

# Read one jsonpath field off a deployment. Bounded (--request-timeout, three tries) and
# DELIBERATELY NON-FATAL: it echoes the value, or nothing at all when the control plane
# cannot be reached. Callers treat empty as "unknown", never as an error, because every
# caller is a read that runs AFTER the change it is observing.
kube_read() {
  local name=$1 path=$2 attempt value
  for attempt in 1 2 3; do
    if value=$(kubectl get "deployment/${name}" -n aqra --request-timeout=15s \
                 -o jsonpath="${path}" 2>/dev/null); then
      printf '%s' "${value}"
      return 0
    fi
    echo "kubectl get ${name} ${path} failed (attempt ${attempt}/3); retrying..." >&2
    sleep 2
  done
  echo "kubectl get ${name} ${path} did not answer; continuing without it." >&2
  return 0
}

deploy_and_wait() {
  local name=$1
  local timeout=${2:-10m}
  local force_restart=${3:-no}

  echo "Deploying ${name}..."
  kubectl apply -f "${KUBERNETES_DIR}/deployment/${name}-deployment.yml"

  # A mutable tag does not roll a Deployment. flask is deployed as :latest, so once
  # its spec has settled, `kubectl apply` is a no-op even though CI just pushed a new
  # image under that tag -- Kubernetes compares the spec, not the registry digest.
  #
  # That is not hypothetical: a NumPy fix was built and pushed, and the node kept
  # crash-looping the SAME pod for another 69 minutes (x324 restarts) on the old
  # image, reporting a stale error that looked exactly like the fix having failed.
  #
  # rollout restart forces a new pod, which (imagePullPolicy: Always) pulls the
  # current tag. Only for mutable-tag workloads: mongo and redis are pinned, and
  # restarting the database on every deploy would be gratuitous.
  #
  # The better end state is deploying by immutable digest or the :${GITHUB_SHA} tag
  # CI already publishes, so a rollout is driven by a real spec change. That needs
  # image substitution at apply time and is deliberately left as a follow-up.
  if [ "${force_restart}" = "restart" ]; then
    echo "Forcing a new ${name} pod so it pulls the current mutable tag..."
    kubectl rollout restart "deployment/${name}" -n aqra
  fi

  # These two reads are READ-ONLY and must never fail the deploy, because by the time
  # they run the mutation has already landed. On 2026-08-19 exactly that happened: the
  # rollout restart was issued, this next line hit `net/http: TLS handshake timeout`, and
  # `set -e` aborted the script -- reporting failure for a rollout that was already under
  # way, on a run where every apply had reported `unchanged`.
  #
  # So they are bounded, retried, and non-fatal. `rollout status` below carries its own
  # --timeout and is the real gate: an unreadable generation costs a slower path through
  # it, never a red deploy.
  local generation observed
  generation=$(kube_read "${name}" '{.metadata.generation}')
  if [ -n "${generation}" ]; then
    for _ in $(seq 1 60); do
      observed=$(kube_read "${name}" '{.status.observedGeneration}')
      # An unreadable observedGeneration is not "behind" -- stop polling a control plane
      # that is not answering and let rollout status make the call.
      if [ -z "${observed}" ] || [ "${observed}" -ge "${generation}" ]; then
        break
      fi
      sleep 1
    done
  else
    echo "Could not read ${name}'s generation; skipping the observed-generation wait."
  fi

  if ! kubectl rollout status "deployment/${name}" -n aqra --watch=true --timeout="${timeout}"; then
    echo "=============================================================="
    echo "ERROR: ${name} did not become ready. Diagnostics follow."
    echo "=============================================================="
    echo "--- pods ---"
    kubectl get pods -n aqra -l "io.kompose.service=${name}" -o wide || true
    echo "--- describe (events are the useful part) ---"
    kubectl describe pod -n aqra -l "io.kompose.service=${name}" | tail -40 || true
    echo "--- current container logs ---"
    kubectl logs -n aqra -l "io.kompose.service=${name}" --tail=60 --all-containers || true
    echo "--- previous container logs (populated if it crash-looped) ---"
    kubectl logs -n aqra -l "io.kompose.service=${name}" --tail=60 --all-containers --previous || true
    dump_dependency_state "${name}"
    return 1
  fi
}

# Everything about the namespace EXCEPT the deployment that failed.
#
# The 2026-08-18 deploys are why this exists. flask crash-looped on
# `redis.exceptions.ConnectionError: Error 111 connecting to redis:6379. Connection
# refused.` -- and the diagnostics above are all scoped to `-l io.kompose.service=flask`,
# so the log said nothing whatsoever about redis: not its pod state, not its restart
# count, not its events. The deploy reported redis "successfully rolled out" seconds
# earlier in the same run, which is a contradiction the log had no way to explain.
#
# `get endpoints` is the decisive line and the reason this function is worth having: a
# Service with no ready backends is exactly what produces ECONNREFUSED, and it is
# invisible from the failing pod's own diagnostics. If redis shows `<none>` there, that is
# the answer; if it lists an IP:6379, the fault is not endpoint readiness and the next
# suspect is the redis process or the network path.
dump_dependency_state() {
  local failed=$1

  echo "=============================================================="
  echo "Namespace state (${failed} depends on these; a failure here is usually the cause)"
  echo "=============================================================="

  # Not `|| true` on its own: label every section so an EMPTY result is visibly empty
  # rather than indistinguishable from a section that never ran.
  echo "--- endpoints (a Service with no backends is what refuses connections) ---"
  kubectl get endpoints -n aqra -o wide 2>&1 | sed 's/^/  /' || echo "  (could not read endpoints)"

  echo "--- all pods in the namespace (restart counts are the tell) ---"
  kubectl get pods -n aqra -o wide 2>&1 | sed 's/^/  /' || echo "  (could not read pods)"

  echo "--- recent namespace events (OOMKilled and evictions land here) ---"
  kubectl get events -n aqra --sort-by=.lastTimestamp 2>&1 | tail -25 | sed 's/^/  /' \
    || echo "  (could not read events)"

  for dependency in mongo redis redis-queue; do
    # An explicit `if`, not `[ ... ] && continue`: under `set -e` a false test makes the
    # AND-list return non-zero as the last command in the loop body, which exits the whole
    # deploy. The diagnostics must never be able to kill the run they are explaining.
    if [ "${dependency}" = "${failed}" ]; then
      continue
    fi
    echo "--- ${dependency} logs ---"
    kubectl logs -n aqra -l "io.kompose.service=${dependency}" --tail=30 --all-containers 2>&1 \
      | sed 's/^/  /' || echo "  (no ${dependency} logs)"
  done
}

echo "Applying base resources..."
kubectl apply -k ${KUBERNETES_DIR}

# MongoDB first to avoid dependency issues.
deploy_and_wait mongo 10m
deploy_and_wait redis 10m
deploy_and_wait redis-queue 10m
# "restart" because both run the mutable :latest tag -- see the note in deploy_and_wait.
deploy_and_wait flask 10m restart
# Longer: the worker runs bootstrap (the location refresh and the CSV rebuild from Mongo)
# before it reports anything, which on a cold volume is minutes.
deploy_and_wait worker 20m restart
