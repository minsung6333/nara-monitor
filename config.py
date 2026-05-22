# 나라장터 수집 설정
import os
from dotenv import load_dotenv
load_dotenv()

SERVICE_KEY = os.getenv(
    "NARA_SERVICE_KEY",
    "U7NShPowQw5tNwWpC1/w9rv0nVSPN+sd0675/+2kSwGg/z0PU+74JtYTnFYrE8I67pYbRapjenOTPDM7hd4X5A==",
)

# 검색 키워드 목록 (공고명/품명에 포함된 것만 수집)
KEYWORDS = [
    'AI',
    '인공지능',
    '데이터',
    '소프트웨어',
]

# 수집 대상 업무 구분 (용역/물품/공사 중 선택)
COLLECT_SERVC  = True   # 용역
COLLECT_THNG   = True   # 물품
COLLECT_CNSTWK = False  # 공사

# API 엔드포인트
ENDPOINTS = {
    'bid_servc':  'https://apis.data.go.kr/1230000/ad/BidPublicInfoService/getBidPblancListInfoServcPPSSrch',
    'bid_thng':   'https://apis.data.go.kr/1230000/ad/BidPublicInfoService/getBidPblancListInfoThngPPSSrch',
    'bid_cnstwk': 'https://apis.data.go.kr/1230000/ad/BidPublicInfoService/getBidPblancListInfoCnstwkPPSSrch',

    'spec_servc':  'https://apis.data.go.kr/1230000/ao/HrcspSsstndrdInfoService/getPublicPrcureThngInfoServcPPSSrch',
    'spec_thng':   'https://apis.data.go.kr/1230000/ao/HrcspSsstndrdInfoService/getPublicPrcureThngInfoThngPPSSrch',
    'spec_cnstwk': 'https://apis.data.go.kr/1230000/ao/HrcspSsstndrdInfoService/getPublicPrcureThngInfoCnstwkPPSSrch',

    'plan_servc':  'https://apis.data.go.kr/1230000/ao/OrderPlanSttusService/getOrderPlanSttusListServcPPSSrch',
    'plan_thng':   'https://apis.data.go.kr/1230000/ao/OrderPlanSttusService/getOrderPlanSttusListThngPPSSrch',
    'plan_cnstwk': 'https://apis.data.go.kr/1230000/ao/OrderPlanSttusService/getOrderPlanSttusListCnstwkPPSSrch',
}
