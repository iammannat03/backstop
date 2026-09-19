# Custom build for `sam build`. Every function uses CodeUri: . because the
# handlers import each other's top-level packages, but copying the whole repo
# into each artifact would drag in .venv, the ui, and the opa binary, so this
# copies only the Python packages the handlers need. boto3 is provided by the
# Lambda runtime and is not installed here. The opa binary ships in the
# OpaLayer, not in the function package.
#
# The wheel platform below must match Globals.Function.Architectures in
# template.yaml (arm64 maps to manylinux2014_aarch64, x86_64 to
# manylinux2014_x86_64).

PACKAGES := shared persistence worker_agent governance verifier_agent execution command_agent audit ingestion
WHEEL_PLATFORM ?= manylinux2014_aarch64

build-%:
	python3 -m pip install -r requirements.txt -t "$(ARTIFACTS_DIR)" \
		--platform $(WHEEL_PLATFORM) --python-version 3.12 \
		--implementation cp --only-binary=:all: --upgrade
	for p in $(PACKAGES); do \
		rsync -a --exclude '__pycache__' --exclude 'layer' "$$p" "$(ARTIFACTS_DIR)/"; \
	done
