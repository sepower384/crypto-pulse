"""읽는 사람을 위한 한국어화: 영어 이름·용어를 한국어로, 금액·숫자를 한국식으로.

강회장 지시(2026-09-17): 친절하게, 영어는 최대한 쓰지 않는다.
프로젝트·코인 이름처럼 바꿀 수 없는 고유명사만 영어로 남긴다.
"""
import re

CHAIN_KR = {
    "Ethereum": "이더리움", "Solana": "솔라나", "Base": "베이스", "Arbitrum": "아비트럼", "BSC": "BNB 체인",
    "BNB Chain": "BNB 체인", "Binance-Peg": "BNB 체인", "Robinhood": "로빈후드 체인", "Robinhood Chain": "로빈후드 체인",
    "Arc": "아크(서클 체인)", "Polygon": "폴리곤", "Polygon POS": "폴리곤", "Hyperliquid L1": "하이퍼리퀴드",
    "Hyperliquid": "하이퍼리퀴드", "HyperEVM": "하이퍼리퀴드", "Sui": "수이", "Tron": "트론", "Avalanche": "아발란체",
    "OP Mainnet": "옵티미즘", "Optimism": "옵티미즘", "Plasma": "플라스마", "Monad": "모나드", "Stellar": "스텔라",
    "Bitcoin": "비트코인", "Flare": "플레어", "Plume Mainnet": "플룸", "Plume": "플룸", "Citrea": "시트리아",
    "Starknet": "스타크넷", "Mantle": "맨틀", "Linea": "리네아", "Ink": "잉크", "Sonic": "소닉", "Katana": "카타나",
    "MegaETH": "메가이더", "Unichain": "유니체인", "Berachain": "베라체인", "Aptos": "앱토스", "TON": "톤",
    "Scroll": "스크롤", "ZKsync Era": "지케이싱크", "zkSync Era": "지케이싱크", "Blast": "블라스트", "Sei": "세이",
    "Cardano": "카르다노", "XRPL": "리플 원장", "Near": "니어", "Cosmos": "코스모스", "Osmosis": "오스모시스",
    "Injective": "인젝티브", "Celo": "셀로", "Gnosis": "노시스", "Fantom": "팬텀", "Core": "코어", "CORE": "코어",
    "Kaia": "카이아", "Abstract": "앱스트랙트", "Soneium": "소니움", "World Chain": "월드체인", "Zora": "조라",
    "Sophon": "소폰", "Stable": "스테이블 체인", "Anubis": "아누비스", "Babylon Genesis": "바빌론",
    "solana": "솔라나", "ethereum": "이더리움", "base": "베이스", "bsc": "BNB 체인", "arbitrum": "아비트럼",
    "arc": "아크(서클 체인)", "robinhood": "로빈후드 체인", "sui": "수이", "hyperliquid": "하이퍼리퀴드",
    "Eth": "이더리움", "eth": "이더리움",
}

OUTLET_KR = {"CoinDesk": "코인데스크", "The Block": "더블록", "Decrypt": "디크립트", "Cointelegraph": "코인텔레그래프",
             "The Defiant": "디파이언트", "블록미디어": "블록미디어", "토큰포스트": "토큰포스트", "코인니스": "코인니스"}

SUBREDDIT_KR = {"CryptoCurrency": "코인 종합 게시판", "Bitcoin": "비트코인 게시판", "ethereum": "이더리움 게시판",
                "CryptoMarkets": "코인 시장 게시판", "solana": "솔라나 게시판", "altcoin": "알트코인 게시판",
                "defi": "디파이 게시판", "ethtrader": "이더리움 트레이더 게시판", "CryptoMoonShots": "급등 기대 코인 게시판",
                "SatoshiStreetBets": "코인 단타 게시판", "memecoins": "밈코인 게시판", "ethfinance": "이더리움 투자 게시판",
                "CryptoCurrencyTrading": "코인 트레이딩 게시판"}

NAME_KR = {"Vitalik Buterin": "비탈릭 부테린", "Vitalik": "비탈릭 부테린", "Michael Saylor": "마이클 세일러",
           "saylor": "마이클 세일러", "CZ": "창펑 자오(CZ)", "Brian Armstrong": "브라이언 암스트롱",
           "Arthur Hayes": "아서 헤이즈", "Anthony Pompliano": "앤서니 폼플리아노", "Raoul Pal": "라울 팔",
           "Mike Novogratz": "마이크 노보그라츠", "Cathie Wood": "캐시 우드", "Balaji": "발라지 스리니바산",
           "Anatoly Yakovenko": "아나톨리 야코벤코", "Hayden Adams": "헤이든 애덤스", "Haseeb Qureshi": "하시브 쿠레시",
           "ZachXBT": "재크XBT", "Lookonchain": "룩온체인", "Wu Blockchain": "우블록체인", "Peter Schiff": "피터 시프",
           "Elon Musk": "일론 머스크", "Lyn Alden": "린 알덴", "Watcher Guru": "워처구루", "Dan Romero": "댄 로메로",
           "Jesse Pollak": "제시 폴락", "Linda Xie": "린다 시에", "Chris Dixon": "크리스 딕슨", "Tim Beiko": "팀 베이코",
           "Vitalik 블로그": "비탈릭 부테린 블로그", "Arthur Hayes 에세이": "아서 헤이즈 에세이"}

SOURCE_KR = {"x": "엑스(트위터)", "reddit": "레딧", "bluesky": "블루스카이", "farcaster": "파캐스터",
             "telegram": "텔레그램 채널", "blogs": "유명인 블로그", "coingecko": "코인게코",
             "kr_community": "국내 커뮤니티", "kr_news": "국내 속보·거래소 공지", "news": "언론 기사",
             "onchain": "블록체인 거래 데이터"}

# 번역문에 남는 영어 용어 → 한국어
_TERMS = [
    (r"\bDeFi\b", "디파이"), (r"\bDEXs?\b", "탈중앙 거래소"), (r"\bCEXs?\b", "중앙화 거래소"), (r"\bTVL\b", "예치금"),
    (r"\bL2s?\b", "레이어2"), (r"\bL1s?\b", "레이어1"), (r"\b[Ss]tablecoins?\b", "스테이블코인"),
    (r"\b[Mm]eme ?coins?\b", "밈코인"), (r"\b[Aa]irdrops?\b", "에어드랍"), (r"\b[Mm]ainnet\b", "메인넷"),
    (r"\b[Tt]estnet\b", "테스트넷"), (r"\b[Oo]n-?chain\b", "온체인"), (r"\b[Ww]hales?\b", "고래"),
    (r"\b[Ll]iquidations?\b", "강제청산"), (r"\b[Bb]ullish\b", "상승 기대"), (r"\b[Bb]earish\b", "하락 우려"),
    (r"\b[Pp]erps?\b", "무기한 선물"), (r"\b[Yy]ield\b", "수익"), (r"\b[Ss]taking\b", "스테이킹"),
    (r"\b[Tt]okeniz(?:ation|ed)\b", "토큰화"), (r"\bRWAfi\b", "실물자산 금융"),
]
_TERMS_RX = [(re.compile(p), r) for p, r in _TERMS]


def ko_terms(text):
    for rx, rep in _TERMS_RX:
        text = rx.sub(rep, text or "")
    return text


def chain(name):
    return CHAIN_KR.get(name, CHAIN_KR.get((name or "").strip(), name or "?"))


def chains(names, limit=3):
    return " · ".join(dict.fromkeys(chain(n) for n in (names or [])[:limit])) or "정보 없음"


def outlet(name):
    return OUTLET_KR.get(name) or NAME_KR.get(name, name)


def subreddit(handle):
    sub = (handle or "").removeprefix("r/")
    return SUBREDDIT_KR.get(sub, f"{sub} 게시판")


def person(name):
    return NAME_KR.get(name, name)


def dollars(v):
    """달러 금액을 한국식 단위로: 85,700,000 → '약 8,570만 달러', 1.2e9 → '약 12억 달러'."""
    if v is None:
        return "알 수 없음"
    a = abs(v)
    sign = "-" if v < 0 else ""
    if a >= 1e8:
        eok = a / 1e8
        return f"약 {sign}{eok:,.1f}억 달러".replace(".0억", "억")
    if a >= 1e4:
        return f"약 {sign}{round(a / 1e4):,}만 달러"
    return f"약 {sign}{a:,.0f}달러"


def count(n):
    """조회수 등: 12000 → '1.2만', 1700 → '1,700'."""
    n = int(n or 0)
    if n >= 10_000:
        return f"{n / 10_000:.1f}만".replace(".0만", "만")
    return f"{n:,}"


def change(v):
    if v is None:
        return "알 수 없음"
    return f"{v:+.0f}%"
