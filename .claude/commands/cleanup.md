Run `make cleanup` to tear down beaconry test stacks and remove images beaconry's own tests pulled or built (BC-080).

The script is `poc/scripts/cleanup.py` — review the removable/preserved allowlist there for which images are in scope.

After running, report: image counts removed vs. preserved, and whether any test containers are still visible via `docker ps -a | grep -E "beaconry|probe-target|telegram-mock"`.
