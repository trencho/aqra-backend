# ⚠️ TEMPORARY — remove this stage once the deploy node's CPU is fixed. See below.
#
# NumPy's published manylinux wheels are compiled with an x86-64-v2 baseline
# (SSE3/SSSE3/SSE4.1/SSE4.2/POPCNT). The deploy node does not have those
# instructions, so importing NumPy aborts the process outright:
#
#     Error: NumPy was built with baseline optimizations:
#     (X86_V2) but your machine doesn't support: (X86_V2).
#
# gunicorn imported the app, NumPy died, the container exited, and the pod
# crash-looped 81 times in 19 minutes. It is the same root cause as MongoDB
# being stuck on 4.4 here: the node also has no AVX. Both point at a VM using a
# generic CPU model (e.g. QEMU's default) that masks features the physical host
# probably has.
#
# Rebuilding NumPy with `cpu-baseline=none` produces a build that runs on any
# x86 CPU, selecting optimised code paths at runtime instead of requiring them
# at load time. Chosen over downgrading NumPy below 2.0, which has no cp314
# wheels and would have dragged Python off 3.14 and cascaded into pandas,
# scipy and scikit-learn.
#
# TO REVERT once the hypervisor exposes a modern CPU model: delete this stage
# and the "Reinstall NumPy" step in the runtime image. Nothing else changed --
# no version was downgraded, so there is nothing to restore.
FROM python:3.14.7-slim AS numpy-builder

RUN apt-get update && \
    apt-get install -y --no-install-recommends build-essential && \
    rm -rf /var/lib/apt/lists/*

# The version is read from requirements rather than repeated here, so a
# Dependabot bump cannot silently leave this stage building a stale NumPy.
RUN --mount=type=cache,target=/root/.cache/pip \
    --mount=type=bind,source=requirements,target=requirements \
    NUMPY_VERSION="$(grep -iE '^numpy==' requirements/common.txt | cut -d= -f3)" && \
    if [ -z "${NUMPY_VERSION}" ]; then echo "ERROR: no numpy== pin found in requirements/common.txt" >&2; exit 1; fi && \
    echo "Building NumPy ${NUMPY_VERSION} with cpu-baseline=none" && \
    pip wheel --no-cache-dir --no-deps --no-binary=numpy -w /wheels \
        --config-settings=setup-args="-Dcpu-baseline=none" \
        "numpy==${NUMPY_VERSION}"


FROM python:3.14.7-slim

LABEL maintainer="Aleksandar Trenchevski <atrenchevski@gmail.com>"

# Prevents Python from writing pyc files and keeps Python from buffering stdout and stderr to avoid situations where
# the application crashes without emitting any logs due to buffering.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# The app user has no home directory, so matplotlib's default config dir under ~ fails and it
# falls back to a fresh temp dir, and a cold font cache, in every process.
ENV MPLCONFIGDIR=/tmp/matplotlib

# Install system dependencies.
RUN apt update && \
    apt upgrade -y && \
    apt dist-upgrade -y && \
    apt install -y --no-install-recommends libgomp1 nginx tzdata && \
    apt autoclean clean && \
    apt autoremove -y && \
    rm -rf /var/lib/apt/lists/* /tmp/* /var/tmp/*

# Set the working directory
WORKDIR /app

# Download dependencies as a separate step to take advantage of Docker's caching.
# Leverage a cache mount to /root/.cache/pip to speed up subsequent builds.
# Leverage a bind mount to requirements.txt to avoid having to copy them into
# into this layer.
RUN --mount=type=cache,target=/root/.cache/pip \
    --mount=type=bind,source=requirements,target=requirements \
    pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements/prod.txt

# ⚠️ TEMPORARY, paired with the numpy-builder stage above — see the comment there.
# Replaces the x86-64-v2 wheel pip just installed with the baseline-free build.
# Same version, so this is a swap of machine code, not a downgrade.
COPY --from=numpy-builder /wheels /tmp/wheels
#
# The check fails the BUILD if the rebuilt NumPy still demands SSE4/AVX, rather
# than letting the node discover it by crash-looping. Introspection is
# best-effort: the config key names are not a stable API, so a missing key reads
# as an empty baseline and passes, and the build log remains the final word.
RUN pip install --no-cache-dir --force-reinstall --no-deps /tmp/wheels/numpy-*.whl && \
    rm -rf /tmp/wheels && \
    python -c "import numpy, sys; b = [str(f).upper() for f in numpy.__config__.show(mode='dicts').get('SIMD Extensions', {}).get('baseline', [])]; print('NumPy', numpy.__version__, '| baseline:', b or '(none - runs on any x86_64)'); bad = [f for f in b if f.startswith(('SSE3', 'SSSE3', 'SSE4', 'AVX', 'X86_V'))]; sys.exit('ERROR: NumPy baseline still requires %s - cpu-baseline=none did not apply' % bad) if bad else None"

# Copy the source code into the container.
COPY . .

# Copy the nginx configuration file.
COPY ./docker/nginx.conf /etc/nginx/nginx.conf

# Run unprivileged.
#
# Two daemons share this container: nginx (reverse proxy) and gunicorn (WSGI), started by
# docker/launch.sh. Both had to be moved off root-owned paths for this to work:
#   - nginx logs to stdout/stderr instead of /var/log/nginx, and its pid file moved to /tmp
#   - gunicorn's pid file and the unix socket nginx proxies to moved from /run to /tmp
#   - nginx's temp directories under /var/lib/nginx are chowned below; nginx creates files there
#     for buffered request and proxy bodies, and the paths are compiled in rather than configured
#
# The listen port moved from 80 to 8080, because binding below 1024 needs CAP_NET_BIND_SERVICE.
#
# Application data is untouched by any of this: every write goes to $VOLUME_PATH (/var/api in the
# cluster), which is a mounted volume. Its ownership is handled by the init container in
# kubernetes/deployment/flask-deployment.yml, because kubelet does not apply fsGroup to a hostPath.
RUN groupadd --system --gid 1001 app && \
    useradd --system --uid 1001 --gid app --no-create-home --shell /usr/sbin/nologin app && \
    mkdir -p /var/lib/nginx/body /var/lib/nginx/proxy /var/lib/nginx/fastcgi \
             /var/lib/nginx/uwsgi /var/lib/nginx/scgi && \
    chown -R app:app /var/lib/nginx

# The data volume's mount point, owned by the app user. A new Docker named volume copies the
# ownership of the directory it is mounted over, so compose's flask-data volume comes up
# writable; without this it was root-owned and the app died on its first mkdir. Kubernetes
# mounts a hostPath, which ignores this, hence the init container there.
RUN mkdir -p /var/api && chown app:app /var/api

EXPOSE 8080

USER app

# Run the application.
ENTRYPOINT ["/bin/bash", "/app/docker/launch.sh"]