# SPDX-License-Identifier: GPL-3.0-or-later

PREFIX ?= /usr
DESTDIR ?=
APPID := io.github.ghelperlinux.GHelper
NAME := ghelper-linux
VERSION := $(shell python3 -c "import sys; sys.path.insert(0, 'src'); import ghelper; print(ghelper.APP_VERSION)")
RPMTOP ?= $(HOME)/rpmbuild
LIBDIR := $(DESTDIR)$(PREFIX)/lib/ghelper-linux
BINDIR := $(DESTDIR)$(PREFIX)/bin
SHAREDIR := $(DESTDIR)$(PREFIX)/share
METAINFODIR := $(SHAREDIR)/metainfo
UNITDIR := $(DESTDIR)$(PREFIX)/lib/systemd/user
UDEVDIR := $(DESTDIR)$(PREFIX)/lib/udev/rules.d

.PHONY: run check test install uninstall icon rpm

run:
	PYTHONPATH=src python3 -m ghelper

check:
	PYTHONPATH=src python3 -m ghelper --check

test:
	PYTHONPATH=src python3 -m unittest discover -s tests -v

icon:
	convert -background none -density 512 src/ghelper/data/$(APPID).svg \
		-resize 128x128 src/ghelper/data/$(APPID).png

$(APPID).png:
	$(MAKE) icon

install: $(APPID).png
	install -d $(LIBDIR)
	rm -rf $(LIBDIR)/ghelper
	cp -r src/ghelper $(LIBDIR)/ghelper
	find $(LIBDIR) -name __pycache__ -type d -prune -exec rm -rf {} +
	install -d $(BINDIR)
	printf '#!/bin/sh\nexec env PYTHONPATH="%s" python3 -m ghelper "$$@"\n' \
		"$(PREFIX)/lib/ghelper-linux" > $(BINDIR)/ghelper-linux
	chmod 755 $(BINDIR)/ghelper-linux
	install -Dm644 data/$(APPID).desktop $(SHAREDIR)/applications/$(APPID).desktop
	install -Dm644 data/$(APPID).metainfo.xml \
		$(METAINFODIR)/$(APPID).metainfo.xml
	install -Dm644 src/ghelper/data/$(APPID).svg \
		$(SHAREDIR)/icons/hicolor/scalable/apps/$(APPID).svg
	install -Dm644 src/ghelper/data/$(APPID).png \
		$(SHAREDIR)/icons/hicolor/128x128/apps/$(APPID).png
	install -Dm644 data/ghelper-automation.service \
		$(UNITDIR)/ghelper-automation.service
	install -Dm644 data/60-ghelper-hotkeys.rules \
		$(UDEVDIR)/60-ghelper-hotkeys.rules
	if [ -z "$(DESTDIR)" ]; then \
		udevadm control --reload-rules 2>/dev/null || true; \
		udevadm trigger --subsystem-match=input 2>/dev/null || true; \
		gtk-update-icon-cache -f -t $(SHAREDIR)/icons/hicolor 2>/dev/null || true; \
	fi

uninstall:
	rm -rf $(LIBDIR)
	rm -f $(BINDIR)/ghelper-linux
	rm -f $(SHAREDIR)/applications/$(APPID).desktop
	rm -f $(METAINFODIR)/$(APPID).metainfo.xml
	rm -f $(SHAREDIR)/icons/hicolor/scalable/apps/$(APPID).svg
	rm -f $(SHAREDIR)/icons/hicolor/128x128/apps/$(APPID).png
	rm -f $(UNITDIR)/ghelper-automation.service
	rm -f $(UDEVDIR)/60-ghelper-hotkeys.rules

rpm:
	mkdir -p $(RPMTOP)/SOURCES
	tar --transform 's,^\.,$(NAME)-$(VERSION),' \
		--exclude='__pycache__' --exclude='*.pyc' --exclude-vcs \
		-czf $(RPMTOP)/SOURCES/$(NAME)-$(VERSION).tar.gz .
	rpmbuild --define "_topdir $(RPMTOP)" --define "gh_version $(VERSION)" \
		-ba packaging/$(NAME).spec
