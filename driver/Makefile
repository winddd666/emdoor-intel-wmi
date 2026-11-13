obj-m += emdoor-wmi.o

KDIR ?= /lib/modules/$(shell uname -r)/build

all:
	$(MAKE) -C $(KDIR) M=$(CURDIR) W=1 KCFLAGS="-Werror" modules

clean:
	$(MAKE) -C $(KDIR) M=$(CURDIR) clean
