import azure.functions as func
import logging
import json
import os
from dotenv import load_dotenv
from langchain_openai import ChatOpenAI, OpenAIEmbeddings
from langchain_core.prompts import PromptTemplate
from langchain_mongodb.vectorstores import MongoDBAtlasVectorSearch
from pymongo import MongoClient

app = func.FunctionApp()

load_dotenv(verbose=True)
logging.info("Starting application initialization...")

# config 파일 로드
with open(f'configs/config.json', 'r') as f:
    config = json.load(f)

# prompt 불러오기
with open(f'model/templates/chat_template.txt', 'r', encoding='utf-8') as file:
    chat_template = file.read()

with open(f'model/templates/resume_template.txt', 'r', encoding='utf-8') as file:
    resume_template = file.read()


# MongoDB 클러스터 URI 설정
os.environ["MONGODB_ATLAS_CLUSTER_URI"] = os.getenv("MONGODB_ATLAS_CLUSTER_URI")
logging.info(f'## db_name : {config["path"]["db_name"]}')

# OpenAI API 키 설정
os.environ["OPENAI_API_KEY"] = os.getenv("OPENAI_KEY")

# Load DB once at startup
db = None

try:
    logging.info("Starting database initialization...")
    client = MongoClient(os.environ["MONGODB_ATLAS_CLUSTER_URI"], ssl=True)
    
    # 두 컬렉션 모두 pymongo Collection 객체로 초기화
    MONGODB_COLLECTION = client[config['path']['db_name']]['foreigner_legalQA']
    TEST_COLLECTION = client[config['path']['db_name']]['foreigner_legal_test']
    
    logging.info("Database initialized successfully.")

except Exception as e:
    logging.error(f"Error loading database: {str(e)}")



# 응답 생성 함수
def generate_ai_response(conversation_history, query, collection):
    try:
        logging.info(f"Generating embedding for query: {query}")
        embedding_model = OpenAIEmbeddings(model=config['embedding_model'])
        query_embedding = embedding_model.embed_query(query)

        # legal_QA 컬렉션용 Vector Search 쿼리 -- 여기 부분 필요에 따라 수정
        results = collection.aggregate([
            {
                "$vectorSearch": {
                    "index": "vector_index",
                    "path": "embedding",
                    "queryVector": query_embedding,
                    "numCandidates": 100,
                    "limit": config['chat_config']['top_k']
                }
            },
            {
                "$project": {
                    "text": 1,           # text 필드만 프로젝션
                    "source": 1,         # 출처 정보도 포함
                    "score": { "$meta": "vectorSearchScore" },
                    "_id": 0
                }
            }
        ])

        results_list = list(results)
        logging.info("\n=== 유사 문서 검색 결과 ===")
        
        if not results_list:
            logging.info("유사한 문서를 찾을 수 없습니다.")
            context = "일반적인 안내 정보..."

        else:
            context = ""
            for idx, result in enumerate(results_list, 1):
                # 유사 문서 로깅
                logging.info(f"\n[유사 문서 {idx}]")
                logging.info(f"유사도 점수: {result.get('score', 'N/A')}")
                logging.info(f"출처: {result.get('source', 'N/A')}")
                logging.info(f"내용: {result.get('text', 'N/A')[:200]}...")  # 앞부분만 로깅

                # 컨텍스트에 추가
                context += f"""
                관련 사례 {idx} (출처: {result.get('source', 'N/A')}):
                {result.get('text', '')}
                """

        # 대화 기록 포맷팅 (가장 최근 대화 3쌍 = 6개 메시지)
        formatted_conversation = "\n".join([
            f"{'사용자' if msg['speaker'] == 'human' else 'AI'}: {msg['utterance']}"
            for msg in conversation_history[-config['chat_config']['prev_turns']:]  # 현재 질문 포함
        ])

        # AI 응답 생성
        llm = ChatOpenAI(
            model=config['chat_config']['model'],
            # temperature=0.3
        )

        prompt_template = PromptTemplate.from_template(chat_template)
        filled_prompt = prompt_template.format(
            context=context,
            conversation_history=formatted_conversation,
            query=query
        )
        
        output = llm.invoke(input=filled_prompt)
        
        return {
            "answer": output.content
        }

    except Exception as e:
        logging.error(f"Error in generate_ai_response: {str(e)}")
        raise



def generate_ai_response_first_query(query, collection):
    try:
        logging.info(f"Generating embedding for query: {query}")
        embedding_model = OpenAIEmbeddings(model=config['embedding_model'])
        query_embedding = embedding_model.embed_query(query)

        # MongoDB Vector Search 쿼리
        results = collection.aggregate([
            {
                "$vectorSearch": {
                    "index": "vector_index",
                    "path": "내담자_정보.Embedding",
                    "queryVector": query_embedding,
                    "exact": True,
                    "limit": config['chat_config']['top_k']
                }
            },
            {
                "$project": {
                    "내담자_정보": 1,
                    "해결방법": 1,
                    "score": { "$meta": "vectorSearchScore" },
                    "_id": 0
                }
            }
        ])

        # 결과 처리 및 컨텍스트 구성
        results_list = list(results)
        logging.info("\n=== 유사 문서 검색 결과 ===")
        
        if not results_list:
            logging.info("유사한 문서를 찾을 수 없습니다.")
        
        context = ""
        for idx, result in enumerate(results_list, 1):
            # 유사 문서 로깅
            logging.info(f"\n[유사 문서 {idx}]")
            logging.info(f"유사도 점수: {result.get('score', 'N/A')}")
            info = result['내담자_정보']
            logging.info(f"거주지역: {info.get('거주지역', 'N/A')}")
            logging.info(f"국적: {info.get('국적', 'N/A')}")
            logging.info(f"체류자격: {info.get('체류자격', 'N/A')}")
            logging.info(f"추가정보: {info.get('추가정보', 'N/A')}")
            logging.info(f"해결방법: {result.get('해결방법', 'N/A')[:100]}...")  # 해결방법은 앞부분만

            # 컨텍스트 구성
            context += f"""
                사례 {idx}:
                - 거주지역: {info.get('거주지역', 'N/A')}
                - 국적: {info.get('국적', 'N/A')}
                - 체류자격: {info.get('체류자격', 'N/A')}
                - 추가정보: {info.get('추가정보', 'N/A')}
                - 해결방법: {result.get('해결방법', 'N/A')}
                """

        # AI 응답 생성
        llm = ChatOpenAI(
            model=config['chat_config']['model']
            # temperature=config['chat_inference']['temperature'],
        )

        prompt_template = PromptTemplate.from_template(chat_template)
        filled_prompt = prompt_template.format(
            context=context,
            conversation_history="",
            query=query
        )
        
        output = llm.invoke(input=filled_prompt)
        
        return {
            "answer": output.content
        }

    except Exception as e:
        logging.error(f"Error in generate_ai_response_first_query: {str(e)}")
        raise




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
            response = generate_ai_response_first_query(user_query, TEST_COLLECTION)
        else:
            response = generate_ai_response(conversation, user_query, MONGODB_COLLECTION)

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


@app.route(route="get_test/{param}", auth_level=func.AuthLevel.ANONYMOUS)
def get_echo_call(req: func.HttpRequest) -> func.HttpResponse:
    param = req.route_params.get('param')
    return func.HttpResponse(json.dumps({"param": param}), mimetype="application/json")
