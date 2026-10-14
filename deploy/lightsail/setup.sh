#!/usr/bin/env bash
# 새 Lightsail 서버(Ubuntu 24.04)에서 한 번만 실행: Docker 설치, 자동 보안 업데이트, 스왑 1GB, 앱 폴더.
# 실행: ssh로 접속한 뒤  bash setup.sh   (sudo 비밀번호 없이 ubuntu 사용자로 실행됨)
set -euo pipefail
sudo apt-get update
sudo apt-get install -y docker.io docker-compose-v2 unattended-upgrades rsync
sudo systemctl enable --now docker
sudo usermod -aG docker "$USER"
# 보안 업데이트 자동 설치(Ubuntu 기본값을 확실히 켬)
echo 'APT::Periodic::Update-Package-Lists "1";
APT::Periodic::Unattended-Upgrade "1";' | sudo tee /etc/apt/apt.conf.d/20auto-upgrades >/dev/null
# 스왑 1GB: 메모리가 잠깐 넘쳐도 앱이 바로 죽지 않게
if [ ! -f /swapfile ]; then
  sudo fallocate -l 1G /swapfile && sudo chmod 600 /swapfile && sudo mkswap /swapfile && sudo swapon /swapfile
  echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab >/dev/null
fi
sudo mkdir -p /opt/gosomi/certs && sudo chown -R "$USER":"$USER" /opt/gosomi   # certs: Supabase CA 인증서 자리
echo "완료. 로그아웃 후 다시 접속하고, /opt/gosomi/.env 를 만든 뒤(.env.example 참고, chmod 600) 맥에서 deploy.sh 를 실행하세요."
