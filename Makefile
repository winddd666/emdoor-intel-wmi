.PHONY: all clean check package

KDIR ?= /lib/modules/$(shell uname -r)/build

all:
	$(MAKE) -C driver KDIR=$(KDIR)

clean:
	$(MAKE) -C driver KDIR=$(KDIR) clean

check: all
	@if [ -f "$(KDIR)/scripts/checkpatch.pl" ]; then \
		perl "$(KDIR)/scripts/checkpatch.pl" --no-tree --strict -f driver/emdoor-wmi.c; \
	fi
	python3 -m py_compile emdoorctl.py package.py test-profile-set-host.py test-mainline-host.py
	python3 test-profile-set-host.py
	python3 test-mainline-host.py

package:
	python3 package.py
