import azure.functions as func
import logging
import json
import sys,os
from dotenv import load_dotenv
from langchain_openai import ChatOpenAI, OpenAIEmbeddings
from langchain_core.prompts import PromptTemplate
from langchain_mongodb.vectorstores import MongoDBAtlasVectorSearch
from pymongo import MongoClient

# 커스텀 ChatModel 클래스 임포트
from model import ChatModel
# SSL 인증서 관리를 위한 라이브러리 ?
import certifi

app = func.FunctionApp()

load_dotenv(verbose=True)

# 로컬 디렉터리에서 로그 파일 저장 경로 설정
log_file_path = os.path.join(os.getcwd(), "logs/function_app.log")  # 현재 디렉터리
print(f"Log file will be saved at: {log_file_path}")

# 기존 핸들러 제거 -> 로그 설정을 꺠끗이 시작
for handler in logging.root.handlers[:]:
    logging.root.removeHandler(handler)

# 로그 설정
logging.basicConfig(
    filename=log_file_path, # 로그 파일 경로
    level=logging.INFO, # 로깅 레벨(INFO 이상의 로그만 기록)
    format="%(asctime)s - %(levelname)s - %(message)s" # 로그 포멧 (시간-레벨-메시지)
)

# 추가 핸들러 (콘솔 로그도 포함)
console_handler = logging.StreamHandler()
console_handler.setLevel(logging.INFO)
formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
console_handler.setFormatter(formatter)
logging.getLogger().addHandler(console_handler)

logging.info("Starting application initialization...")

# 설정 파일(configs) 로드 및 환경 변수 설정
with open(f'configs/config.json', 'r') as f:
    config = json.load(f)

logging.info(f"Config file loaded")

# 환경 변수 설정
os.environ["MONGODB_ATLAS_CLUSTER_URI"] = os.getenv("MONGODB_ATLAS_CLUSTER_URI")
os.environ["OPENAI_API_KEY"] = os.getenv("OPENAI_KEY")

# SSL 인증서 확인
print(certifi.where())



# Load DB once at startup
try:
    logging.info("Starting database initialization...")
    client = MongoClient(os.environ["MONGODB_ATLAS_CLUSTER_URI"],
                         tls=True,
                        tlsCAFile=certifi.where())
    
    # 두 컬렉션 모두 pymongo Collection 객체로 초기화
    MONGODB_COLLECTION = client[config['path']['db_name']][config['path']['collection_name']]
    TEST_COLLECTION = client[config['path']['db_name']][config['path']['test_collection_name']]
    
    chat_model = ChatModel(config)

    logging.info("Database & Model initialized successfully.")

except Exception as e:
    logging.error(f"Error loading database: {str(e)}")



# 사용자 요청 수신
@app.route(route="question", auth_level=func.AuthLevel.ANONYMOUS)
def question(req: func.HttpRequest) -> func.HttpResponse:
    logging.info("Question function triggered.")
    
    try:
        req_body = req.get_json()
        conversation = req_body.get('Conversation', [])
        
        
        if not conversation:
            return func.HttpResponse("No conversation data provided", status_code=400)

        user_query = next((item['utterance'] for item in reversed(conversation) 
                         if item['speaker'] == 'human'), None)
                         
        if user_query is None:
            return func.HttpResponse("No user utterance found", status_code=400)

        logging.info(f"Extracted user query: {user_query}")

        # 첫 번째 쿼리인지 확인
        is_first_query = len([item for item in conversation if item['speaker'] == 'human']) == 1

        if is_first_query:
            logging.info(f"First user query detected: {user_query}")
            response = chat_model.generate_ai_response_first_query(conversation_history="",
                                                                query=user_query,
                                                                collection=TEST_COLLECTION)
        else:
            response = chat_model.generate_ai_response(conversation, user_query, MONGODB_COLLECTION)

        # 응답에서 references 제외하고 answer만 반환
        return func.HttpResponse(
            json.dumps({
                "answer": response["answer"]
            }, ensure_ascii=False),
            mimetype="application/json"
        )

    except Exception as e:
        logging.error(f"Error processing question: {str(e)}")
        return func.HttpResponse(f"An error occurred: {str(e)}", status_code=500)

# for test
@app.route(route="get_test/{param}", auth_level=func.AuthLevel.ANONYMOUS)
def get_echo_call(req: func.HttpRequest) -> func.HttpResponse:
    param = req.route_params.get('param')
    return func.HttpResponse(json.dumps({"param": param}), mimetype="application/json")