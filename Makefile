PYTHON ?= python3
VERSION ?= 0.1.0
RUNTIME_MODELS ?= build/runtime-options/device
DIST ?= dist/$(VERSION)

.PHONY: test artwork-release model-release release verify clean

test:
	$(PYTHON) -m unittest discover -s tests

artwork-release:
	$(PYTHON) tools/release_assets.py --version $(VERSION) \
		--output $(DIST)/artwork

model-release:
	$(PYTHON) tools/build_neutral_models.py --version $(VERSION) \
		--source $(RUNTIME_MODELS) --output $(DIST)/models-unpacked
	$(PYTHON) tools/release_models.py $(DIST)/models-unpacked $(DIST)/models \
		--version $(VERSION)
	$(PYTHON) tools/package_models.py $(DIST)/models-unpacked \
		$(DIST)/trimui-brick-cartridge-models-$(VERSION).tar.gz

release: test artwork-release model-release verify

verify:
	$(PYTHON) tools/verify_release.py $(DIST)/artwork

clean:
	rm -rf dist
