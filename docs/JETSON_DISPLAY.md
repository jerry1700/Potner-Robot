# 젯슨 화면 세팅 — 로봇 얼굴 띄우기

로봇의 얼굴(`face_display`)을 7인치 LCD에 띄우기 위한 젯슨 설정입니다.

**여기 적힌 문제는 전부 코드가 아니라 환경 설정입니다.** 젯슨을 새로 플래시하거나
다른 사람이 세팅하면 같은 곳에서 다시 막힙니다. 2026-07-30 에 네 군데서 막혀
반나절을 썼고, 그중 코드 문제는 하나였습니다.

관련 코드
- [`src/potner_base/potner_base/face_display_node.py`](../src/potner_base/potner_base/face_display_node.py)
- [`src/potner_base/potner_base/face.py`](../src/potner_base/potner_base/face.py) — 표정 도형 (순수 모듈)

---

## 1. 하드웨어 — HDMI 단자가 없습니다

**Jetson Orin Nano 개발자 키트는 DisplayPort 만 냅니다.** 커널에도 커넥터가
하나뿐입니다.

```bash
grep -H . /sys/class/drm/*/status
# /sys/class/drm/card1-DP-1/status:connected
```

7인치 LCD 가 HDMI 입력이면 변환이 필요한데 두 가지를 주의해야 합니다.

| 함정 | 증상 |
|---|---|
| **어댑터 방향이 반대** (`HDMI → DP` 를 샀다) | `disconnected` |
| **패시브 어댑터** | `disconnected` |

방향은 `DP(수) → HDMI(암)` 여야 하고, **액티브(active)** 여야 합니다. Orin Nano 의
DP 는 패시브 어댑터를 지원하지 않습니다. 둘 다 증상이 같아서 — 모니터가 "신호
없음"조차 안 띄우고 그냥 안 켜집니다 — 구분이 안 됩니다.

**모니터에 DP 입력이 있으면 DP-DP 직결이 가장 확실합니다.**

### 케이블은 부팅 전에 연결하세요

젯슨의 DP 는 핫플러그가 잘 안 붙습니다. 켜둔 채로 뽑았다 꽂으면
`disconnected` 로 남는 일이 흔합니다.

```bash
# 재감지 시도 (재부팅 없이)
echo detect | sudo tee /sys/class/drm/card1-DP-1/status > /dev/null
sleep 2 && cat /sys/class/drm/card1-DP-1/status
```

안 되면 **케이블을 연결한 채로 재부팅**하는 것이 확실합니다.

> ⚠️ 시연 중에는 화면 케이블을 만지지 마세요. 세션이 죽으면 SSH 없이
> 복구할 방법이 없습니다.

---

## 2. 자동 로그인 — 없으면 얼굴이 안 뜹니다

로그인 화면에 멈춰 있으면 `:0` 은 `gdm` 소유라서 우리 창을 올릴 수 없습니다.
`e104` 로 SSH 해도 권한이 없습니다.

```bash
loginctl list-sessions
# c1  128 gdm  seat0 tty1     <- 이러면 아무도 로그인하지 않은 상태
```

로봇은 전원만 켜면 얼굴이 떠야 하므로 자동 로그인이 **임시 우회가 아니라 제품
설정**입니다.

```bash
sudo cp /etc/gdm3/custom.conf /etc/gdm3/custom.conf.bak
sudo sed -i \
  's/^#  AutomaticLoginEnable = true$/AutomaticLoginEnable=true/; s/^#  AutomaticLogin = user1$/AutomaticLogin=e104/' \
  /etc/gdm3/custom.conf
grep -n "Automatic\|Wayland" /etc/gdm3/custom.conf
```

이렇게 나와야 합니다.

```
WaylandEnable=false
AutomaticLoginEnable=true
AutomaticLogin=e104
```

`WaylandEnable=false` 는 JetPack 6 에 이미 켜져 있습니다. **그대로 두세요.**
OpenCV 의 창 처리는 GTK/X11 기반이라 Xorg 여야 편합니다.

적용:

```bash
sudo systemctl restart gdm3
```

15초쯤 뒤 `e104` 가 `seat0` 에 나타나야 합니다.

```bash
loginctl list-sessions
# 1 1000 e104 seat0 tty2
```

되돌리려면 `custom.conf.bak` 를 복사하고 gdm 을 재시작하세요.

**자동 로그인은 물리적으로 접근하는 사람이 데스크탑에 들어온다는 뜻입니다.**
로봇에는 이게 맞지만 맞바꾼 것이 있다는 건 알아두세요.

---

## 3. 화면 절전 — 반드시 완전히 끄세요 ★

가장 오래 헤맨 곳입니다.

**증상**: 5분쯤 뒤 화면이 검게 되고 마우스 커서만 남습니다. `gnome-shell` 은
살아 있고 커넥터도 `connected` 인데 아무것도 안 그려집니다.

**원인**: 절전에 들어가거나 깨어날 때 modeset 이 일어나는데, 젯슨의
`nvidia-modeset` 이 그걸 실패합니다.

```
nvidia-modeset: ERROR: GPU:0: Failed to allocate 2743000 KBPS Iso and 4294967295 KBPS Dram
nvidia-modeset: ERROR: GPU:0: Unexpectedly failed to lock to max DRAM pre-modeset!
```

`4294967295` 는 `0xFFFFFFFF` — 드라이버가 대역폭 계산에 실패한 값입니다. 한 번
실패하면 화면이 검게 남습니다.

```bash
journalctl -b --no-pager -p warning | grep -i nvidia-modeset
```

그래서 **절전을 아예 일어나지 않게** 만드는 것이 대책입니다. 로봇 얼굴은 계속
켜져 있어야 하므로 절전을 쓸 이유도 없습니다.

### 세 층을 다 막아야 합니다

**① GNOME** (영구, 사용자 설정)

```bash
export DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/$(id -u)/bus
gsettings set org.gnome.desktop.session idle-delay 0
gsettings set org.gnome.desktop.screensaver lock-enabled false
gsettings get org.gnome.desktop.session idle-delay   # uint32 0
```

`idle-delay 0` 은 "끄지 않음"입니다. **화면 잠금도 함께 끕니다** — 잠기면
비밀번호를 넣어야 하는데 로봇에는 키보드가 없습니다.

**② X 서버** (영구, 시스템 설정)

`xset` 으로 끄면 **세션이 새로 뜰 때마다 초기화됩니다.** gdm 을 재시작하면
타이머가 600초로 돌아옵니다. 그래서 설정 파일에 적습니다.

```bash
sudo mkdir -p /etc/X11/xorg.conf.d
sudo tee /etc/X11/xorg.conf.d/10-noblank.conf > /dev/null <<'EOF'
Section "ServerFlags"
    Option "BlankTime"   "0"
    Option "StandbyTime" "0"
    Option "SuspendTime" "0"
    Option "OffTime"     "0"
EndSection
EOF
```

**③ 지금 세션** (재부팅 전까지)

```bash
export DISPLAY=:0
xset s off; xset -dpms; xset s noblank
xset q | grep -A3 -E "Screen Saver|DPMS"
```

확인할 두 줄입니다.

```
timeout:  0
DPMS is Disabled
```

### 이미 검게 됐을 때 복구

```bash
export DISPLAY=:0
xrandr --output "$(xrandr -q | awk '/ connected/{print $1; exit}')" --auto
```

안 되면 `sudo systemctl restart gdm3`, 그것도 안 되면 재부팅입니다.
**gdm 을 재시작하면 위 ③ 을 다시 해야 합니다.**

---

## 4. 얼굴 띄우기

### SSH 에서 띄울 때

```bash
export DISPLAY=:0
ros2 run potner_base face_display
```

`DISPLAY` 를 안 주면 노드가 창을 열지 않고 조용히 물러납니다. 얼굴을 못 그리는
것 때문에 주행과 안전 정지가 막히면 안 되기 때문입니다.

`Authorization required, but no authorization protocol specified` 가 나오면
**아직 데스크탑에 로그인하지 않은 것**입니다. 2절을 보세요. (`XAUTHORITY` 를
gdm 의 경로로 지정해도 uid 가 달라 읽을 수 없습니다.)

### 해상도

`width` · `height` 를 `0` 으로 두면 커널이 보고하는 선호 모드를 읽습니다.

```bash
cat /sys/class/drm/*/modes | head -1   # 1024x600
```

7인치 LCD 는 1024x600 입니다. 시험용 모니터와 바꿔 꽂아도 설정을 고칠 필요가
없습니다.

**얼굴은 짧은 변을 한 변으로 하는 정사각형 안에 그립니다.** 1024x600 이면
600x600 이고 좌우에 212px 여백이 생깁니다. 정규화 좌표에 가로세로를 그대로
곱하면 눈이 양옆으로 벌어지고 얼굴이 늘어나기 때문입니다.

### 종료

전체화면이라 창 닫기 버튼이 없습니다. **ESC** 로 닫습니다.

---

## 5. 증상별 정리

| 증상 | 원인 | 절 |
|---|---|---|
| 모니터가 아예 안 켜짐, `disconnected` | 어댑터 방향/패시브, 핫플러그 | 1 |
| 로그인 화면만 뜸 | 자동 로그인 없음 | 2 |
| `Authorization required` | 데스크탑에 로그인 안 됨 | 2 |
| 5분 뒤 검게 되고 커서만 | **nvidia-modeset 실패** | 3 |
| gdm 재시작 후 또 검게 됨 | `xset` 이 초기화됨 | 3② |
| 로그는 "표시 시작"인데 화면에 없음 | (해결됨) OpenCV GTK 순서 | — |
| `Package 'potner_base' not found` | 워크스페이스 source 안 함 | — |

마지막 두 개 중 첫째는 코드 문제였고 고쳤습니다. OpenCV 의 GTK 백엔드는 첫
`imshow` 전까지 창을 만들지 않아서, 그 전에 전체화면을 걸면 조용히 무시됩니다.

---

## 6. 아직 안 한 것 — 부팅 자동 시작

지금은 얼굴을 띄우려고 **GNOME 데스크탑 전체를 켜고 그 위에 창을 올립니다.**
GNOME 이 죽으면 얼굴도 사라집니다.

로봇의 최종 형태는 데스크탑 없이 X 에 얼굴 앱만 올리는 것(키오스크)입니다.
그러면 이 문서의 2절·3절 문제가 대부분 사라지고, 부팅하면 바로 얼굴이 뜹니다.

**시연 전에는 해두는 것이 좋습니다.** 지금 구조로는 시연 중에 화면이 죽으면
SSH 로 붙어 복구해야 합니다.
