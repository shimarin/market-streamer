.PHONY: all install clean

BIN_DIR=$(HOME)/.local/bin
SYSTEMD_USER_DIR=$(HOME)/.config/systemd/user

all:
	@echo "Use 'make install' to install the script."

install:
	@echo "Installing script..."
	mkdir -p $(BIN_DIR)
	cp -v market-streamer.py $(BIN_DIR)/market-streamer && chmod +x $(BIN_DIR)/market-streamer
	cp -v market-streamer.service $(SYSTEMD_USER_DIR)/market-streamer.service
	cp -v p2pool2mqtt.py $(BIN_DIR)/p2pool2mqtt && chmod +x $(BIN_DIR)/p2pool2mqtt
	cp -v polo2mqtt.py $(BIN_DIR)/polo2mqtt && chmod +x $(BIN_DIR)/polo2mqtt
	cp -v polo2mqtt.service $(SYSTEMD_USER_DIR)/polo2mqtt.service
	cp -v poloprivate2mqtt.py $(BIN_DIR)/poloprivate2mqtt && chmod +x $(BIN_DIR)/poloprivate2mqtt
	cp -v poloprivate2mqtt.service $(SYSTEMD_USER_DIR)/poloprivate2mqtt.service
	cp -v sekai-kabuka2mqtt.py $(BIN_DIR)/sekai-kabuka2mqtt && chmod +x $(BIN_DIR)/sekai-kabuka2mqtt
	cp -v sekai-kabuka2mqtt.service $(SYSTEMD_USER_DIR)/sekai-kabuka2mqtt.service
