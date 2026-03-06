PYTHON ?= python3

.PHONY: verify-list verify-core verify-worker verify-api-core verify-frontend verify-frontend-logs

verify-list:
	$(PYTHON) platform/scripts/verify_harness.py --list-targets

verify-core:
	$(PYTHON) platform/scripts/verify_harness.py --target core --run

verify-worker:
	$(PYTHON) platform/scripts/verify_harness.py --target worker --run

verify-api-core:
	$(PYTHON) platform/scripts/verify_harness.py --target api-core --run

verify-frontend:
	$(PYTHON) platform/scripts/verify_harness.py --target frontend --run

verify-frontend-logs:
	$(PYTHON) platform/scripts/verify_harness.py --target frontend-logs --run
