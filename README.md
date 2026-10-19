# ht-fan

현대HT 홈서비스(`www2.hthomeservice.com`) 세대 환기를 커맨드라인에서 켜고 끄는 스크립트.
웹/앱 없이 아침저녁 자동 환기를 돌리려고 만들었다.

## 설치

```bash
pip install "httpx[http2]" cryptography
cp .env.example .env     # HT_ID / HT_PW 입력
```

## 사용

```bash
python ht_fan.py status            # 현재 상태
python ht_fan.py devices           # 환기 기기 목록
python ht_fan.py on --wind mid     # 켜기 (풍량 light | mid | pow)
python ht_fan.py off               # 끄기
python ht_fan.py wind pow          # 풍량만 변경
```

풍량은 `light` / `mid` / `pow` 세 단계다. `low` / `high` / `1` / `2` / `3` 으로 줘도 알아서 변환한다.
전원을 켜면서 풍량을 같이 주면 월패드가 풍량을 무시해서, `on --wind` 는 전원을 먼저 켠 뒤 풍량을 따로 보낸다.

## 설정 (.env)

| 키 | 설명 |
|---|---|
| `HT_ID`, `HT_PW` | 홈서비스 계정 |
| `HT_SITE_ID`, `HT_DONG`, `HT_HO` | 세대 (비우면 계정의 첫 세대) |
| `HT_FAN_ID` | 환기 기기 id (비우면 첫 번째 fan) |
| `HT_TIMEOUT` | 요청 타임아웃 초 (기본 20) |

세션 쿠키는 `.ht_cookies.json` 에 저장해 재사용하고, 만료되면 자동으로 다시 로그인한다.

## 스케줄

상시 켜진 리눅스 서버라면 crontab 으로 돌린다. (시간대는 `CRON_TZ` 로 고정)

```cron
CRON_TZ=Asia/Seoul
0 10 * * * cd ~/ht-fan && ./.venv/bin/python ht_fan.py on --wind mid >> ht_fan.log 2>&1
0 12 * * * cd ~/ht-fan && ./.venv/bin/python ht_fan.py off >> ht_fan.log 2>&1
0 17 * * * cd ~/ht-fan && ./.venv/bin/python ht_fan.py on --wind mid >> ht_fan.log 2>&1
0 19 * * * cd ~/ht-fan && ./.venv/bin/python ht_fan.py off >> ht_fan.log 2>&1
```

## 참고

- `diag` 로 PUT 요청이 막힐 때 HTTP/1.1·HTTP/2·헤더 조합을 바꿔가며 테스트할 수 있다.
- 비공식 API 를 쓰므로 사이트가 바뀌면 동작하지 않을 수 있다. 본인 세대 제어용으로만.
