# config.mock.sh —— G0 本机 mock 配置:零网络、零 sudo,所有路径圈在 dts/mock/ 下。
# 与指向内部地址的 config.example.sh 完全无关;真实配置只在黄区里用。
MOCK_ALLOW=1
KIT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VAR_HOME="$KIT/mock/var"

RUN_AS_ROOT=""
RUN_AS_USER=""

PKG_URL_TEMPLATE="$KIT/mock/pkgs/{BUILD}.tar.gz"   # 本地路径 → 下载走 cp,不碰网络
PKG_CHECKSUM=0

INSTALL_ROOT_STEPS=("bash '$KIT/mock/mock_install_root.sh' '$VAR_HOME' {BUILD}")
INSTALL_USER_STEPS=("bash '$KIT/mock/mock_install_user.sh' '$VAR_HOME' {BUILD}")

CLEAN_USER_STEPS=("bash '$KIT/mock/mock_clean_user.sh' '$VAR_HOME'")
CLEAN_ROOT_STEPS=("true")
CLEAN_PATHS=("$VAR_HOME/slot")

HEALTH_AS=local
HEALTH_CHECK_CMD="grep -q {BUILD} '$VAR_HOME/slot/instance' && grep -qi gaussdb '$VAR_HOME/slot/instance'"

RESET_USER_STEPS=("bash '$KIT/mock/mock_reset.sh' '$VAR_HOME' {BUILD}")
RESET_ROOT_STEPS=()
