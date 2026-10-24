# Out-of-tree compile test of hid-surface against the prepared android17-6.18 tree.
# make -C <ktree> O=<kobj> LLVM=1 LLVM_IAS=1 M=$(PWD) modules
obj-m += hid-surface.o
ccflags-y += -I$(srctree)/drivers/hid
