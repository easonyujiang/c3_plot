.PHONY: setup run dev doctor test build check
setup:
	./scripts/setup
run:
	./c3 --open
dev:
	.venv/bin/python scripts/dev.py
doctor:
	./c3 --doctor
test:
	.venv/bin/python run_tests.py
build:
	npm --prefix frontend run build
check: test build
