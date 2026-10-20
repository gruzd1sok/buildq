PREFIX  ?= $(HOME)/.local
APP_DIR ?= $(HOME)/Applications
VERSION := $(shell sed -n 's/^__version__ = "\(.*\)"/\1/p' bin/buildq)
ARCH    ?= $(shell uname -m)
APP     := build/BuildQBar.app

.PHONY: all app test install install-cli install-app uninstall clean

all: app

app: $(APP)

$(APP): app/Sources/*.swift app/Info.plist app/*.xcstrings bin/buildq
	rm -rf $@
	mkdir -p $@/Contents/MacOS $@/Contents/Resources
	xcrun swiftc -O -target $(ARCH)-apple-macos13.0 -o $@/Contents/MacOS/BuildQBar app/Sources/*.swift
	sed 's/__VERSION__/$(VERSION)/g' app/Info.plist > $@/Contents/Info.plist
	for f in app/*.xcstrings; do xcrun xcstringstool compile $$f -o $@/Contents/Resources; done
	codesign --force --sign - $@

test:
	python3 -m unittest discover -s tests -v

install: install-cli install-app

install-cli:
	install -d $(PREFIX)/bin
	install -m 755 bin/buildq $(PREFIX)/bin/buildq
	@echo "buildq $(VERSION) -> $(PREFIX)/bin/buildq"

install-app: app
	install -d $(APP_DIR)
	rm -rf $(APP_DIR)/BuildQBar.app
	cp -R $(APP) $(APP_DIR)/
	app/install-agent.sh $(APP_DIR)/BuildQBar.app $(PREFIX)/bin/buildq

uninstall:
	app/install-agent.sh --uninstall
	rm -rf $(APP_DIR)/BuildQBar.app $(PREFIX)/bin/buildq
	@echo "state in ~/.buildq is kept; remove it by hand if you want"

clean:
	rm -rf build
