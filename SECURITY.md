# Security policy

## Reporting a vulnerability

Please **do not open a public issue** for a security problem.

Use GitHub's [private vulnerability
reporting](https://github.com/trencho/aqra-backend/security/advisories/new), which opens a draft
advisory visible only to the maintainers. If you would rather use email, write to `trenche` at
`feit.ukim.edu.mk`.

Include what you were doing, what happened, and enough detail to reproduce it. You will get an
acknowledgement. This is a small academic project maintained in spare time, so please allow a
reasonable window before disclosing publicly.

## Scope

This repository is the **API**: the Flask service behind `https://aqra.feit.ukim.edu.mk/api/v1`,
its scheduled data and training jobs, and the Docker and Kubernetes manifests that deploy it. The
web app at the same host is [aqra-frontend](https://github.com/trencho/aqra-frontend), with its own
policy.

The API has no user accounts and no write endpoints. It stores public sensor readings, weather
data and trained models. What it records about visitors is listed in
[`docs/legal/data-processing-facts.md`](docs/legal/data-processing-facts.md).

Secrets reach the service only as environment variables. `kubernetes/sealed-secret/*.yml` are
bitnami SealedSecrets: ciphertext only the cluster's controller can decrypt.

## Supported versions

Only `master` is supported. Fixes land on `master` and go out with the next deploy.

## What already runs

Every pull request runs CodeQL and the test suite. [`security-scan.yml`](.github/workflows/security-scan.yml)
compiles the production and development dependency trees and scans them with OSV. Dependabot
watches pip, GitHub Actions and the Docker base images.
