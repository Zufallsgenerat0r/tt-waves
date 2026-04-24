PROJ = orangecrab
SRC_DIR = ../src

# OrangeCrab r0.2 with ECP5-85F
DEVICE = 85k
PACKAGE = CSFBGA285

VERILOG_SOURCES = $(PROJ).v $(SRC_DIR)/project.v

all: $(PROJ).bit

$(PROJ).json: $(VERILOG_SOURCES)
	yosys -p "read_verilog $^; synth_ecp5 -top top -json $@"

$(PROJ).config: $(PROJ).json $(PROJ).lpf
	nextpnr-ecp5 --$(DEVICE) --package $(PACKAGE) --speed 6 \
		--json $< --textcfg $@ --lpf $(PROJ).lpf

$(PROJ).bit: $(PROJ).config
	ecppack --compress --bit $@ $<

prog: $(PROJ).bit
	dfu-util -d 1209:5af0 -a 0 -D $<

clean:
	rm -f $(PROJ).json $(PROJ).config $(PROJ).bit

.PHONY: all prog clean
