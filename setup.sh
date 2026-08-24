#!/bin/bash
# IO-S 安装脚本
# 用法: cd ~/io-s && bash setup.sh

echo "🔧 IO-S 安装中..."

# 1. 设为可执行
chmod +x ~/io-s/io-s
echo "  ✅ io-s 可执行"

# 2. 添加别名到 .bashrc
if ! grep -q "alias io-s=" ~/.bashrc 2>/dev/null; then
    echo "" >> ~/.bashrc
    echo "# IO-S 别名" >> ~/.bashrc
    echo "alias io-s='python3 ~/io-s/io-s'" >> ~/.bashrc
    echo "  ✅ 别名已添加到 ~/.bashrc"
else
    echo "  ✅ 别名已存在"
fi

# 3. 创建符号链接到 ~/.local/bin（如果存在PATH目录）
if [ -d "$HOME/.local/bin" ]; then
    ln -sf ~/io-s/io-s-wrapper.sh ~/.local/bin/io-s 2>/dev/null || true
fi

echo ""
echo "🎉 IO-S 安装完成!"
echo "运行: source ~/.bashrc && io-s status"
