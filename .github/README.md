# Hosted checks pending

The first shared scoped entrypoint is `python -m tools.check_backend`, which
enforces alignment before Django checks/tests. K06 will add equivalent hosted
CI and remaining approved stages. A local backend pass is not a full product
release gate. Do not add a green placeholder workflow that omits required
frontend, provider, security or final browser acceptance.
