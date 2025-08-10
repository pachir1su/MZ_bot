# MZ_bot

Discord 도박 봇

## 요구 사항
- Python 3.10 이상
- Discord 봇 토큰

## 설치
```bash
pip install -r requirements.txt
```

## 실행
1. `.env` 파일을 생성하고 다음 값을 설정합니다.
   ```env
   DISCORD_TOKEN=봇_토큰
   DEV_GUILD_ID=개발_길드_ID(옵션)
   OWNER_ID=봇_소유자_ID(옵션)
   ```
2. 봇을 실행합니다.
   ```bash
   python main.py
   ```

## 주요 기능
- 10분마다 기본 코인 지급
- 출석 보상, 송금 및 잔액 조회
- 베팅과 순위 확인 등 경제 시스템
- 관리자 전용 설정 및 질의응답 기능

## 봇 초대 링크
https://discord.com/oauth2/authorize?client_id=1403372955546812467

---

### Memo
- 주식 / 코인 밸런스 조정
- 관리자 메뉴 마켓 수정 사용성 개선
