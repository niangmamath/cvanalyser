import os
import openai
import json
import re
from flask import Flask, request, jsonify, send_file
from dotenv import load_dotenv
import pdfplumber
import io
import pytesseract
from pdf2image import convert_from_bytes

load_dotenv()

app = Flask(__name__)

# Configure OpenAI API
openai.api_key = os.getenv("OPENAI_API_KEY")

# Global variable to store the context of the last analysis
last_analysis_context = None

def extract_json_from_string(text):
    """
    Finds and extracts a JSON object from a string.
    """
    match = re.search(r'\{.*\}', text, re.DOTALL)
    if match:
        return match.group(0)
    return text

def analyze_single_resume(job_description, resume_text, filename):
    """
    Analyzes a single resume against a job description, extracting the candidate's name from the content.
    """
    prompt = f"""
    En tant qu'expert en recrutement, analysez le CV ci-dessous par rapport à la description de poste.

    **Description de poste :**
    {job_description}

    **Contenu du CV (provenant du fichier '{filename}'):**
    {resume_text}

    Veuillez :
    1.  **Identifier le nom complet du candidat** à partir du contenu du CV.
    2.  Fournir un score de pertinence de 1 à 100.
    3.  Donner une brève justification pour le score.
    4.  Retourner UNIQUEMENT le résultat au format JSON suivant. Si aucun nom n'est trouvé dans le CV, utilisez le nom du fichier comme solution de secours pour 'candidate_name'.

    {{
      "candidate_name": "<Nom extrait du CV>",
      "score": <score>,
      "justification": "..."
    }}
    """

    try:
        response = openai.ChatCompletion.create(
            model="gpt-3.5-turbo",
            messages=[
                {"role": "system", "content": "You are a recruitment assistant that only responds in valid JSON."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.2,
        )
        content = response.choices[0].message['content']
        json_str = extract_json_from_string(content)
        
        loaded_json = json.loads(json_str)
        # Fallback to filename if the AI fails to extract a name
        if not loaded_json.get("candidate_name") or loaded_json.get("candidate_name") == "<Nom extrait du CV>":
             loaded_json["candidate_name"] = filename

        return loaded_json

    except json.JSONDecodeError:
        return {"error": f"API returned malformed data for {filename}.", "raw_response": content}
    except Exception as e:
        return {"error": f"Error analyzing {filename}: {str(e)}"}

@app.route("/")
def index():
    return send_file('src/index.html')

@app.route("/analyzer")
def analyzer_page():
    return send_file('src/analyzer.html')

@app.route("/analyze", methods=['POST'])
def analyze():
    global last_analysis_context

    if 'job_description' not in request.form or 'resumes' not in request.files:
        return jsonify({"error": "Description de poste ou fichiers de CV manquants."}), 400

    job_description = request.form['job_description']
    resume_files = request.files.getlist('resumes')

    if not resume_files or resume_files[0].filename == '':
         return jsonify({"error": "Veuillez téléverser au moins un fichier de CV."}), 400

    all_results = []
    error_messages = []

    for file in resume_files:
        content = ""
        file_bytes = file.read()
        file.seek(0) # Reset file pointer

        try:
            if file.filename.endswith('.pdf'):
                # First, try reading with pdfplumber
                with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
                    content = "\n".join(page.extract_text() or '' for page in pdf.pages)

                # If no text, it might be an image-based PDF, so use OCR
                if not content.strip():
                    images = convert_from_bytes(file_bytes)
                    ocr_text = []
                    for image in images:
                        ocr_text.append(pytesseract.image_to_string(image))
                    content = "\n".join(ocr_text)
            else:
                content = file_bytes.decode('utf-8', errors='ignore')
            
            if not content.strip():
                error_messages.append(f"Le fichier '{file.filename}' est vide ou illisible, même après tentative d'OCR.")
                continue

        except Exception as e:
             error_messages.append(f"Impossible de traiter le fichier '{file.filename}': {e}")
             continue

        single_analysis = analyze_single_resume(job_description, content, file.filename)

        if 'error' in single_analysis:
            error_messages.append(single_analysis['error'])
        else:
            all_results.append(single_analysis)

    if not all_results and error_messages:
        return jsonify({"error": " ; ".join(error_messages)}), 500

    # Sort and rank the successful results
    if all_results:
        all_results.sort(key=lambda x: x.get('score', 0), reverse=True)
        for i, result in enumerate(all_results):
            result['rank'] = i + 1

    final_response = {"analysis": all_results}
    if error_messages:
        final_response['warnings'] = error_messages

    last_analysis_context = final_response
    return jsonify(final_response)

@app.route("/chat", methods=['POST'])
def chat():
    data = request.get_json()
    user_message = data.get('message')

    if not user_message:
        return jsonify({"error": "No message provided."}), 400

    context_prompt = ""
    if last_analysis_context and 'analysis' in last_analysis_context:
        context_summary = [{'candidat': r.get('candidate_name'), 'score': r.get('score'), 'rang': r.get('rank')} for r in last_analysis_context['analysis']]
        context_prompt = f"""Basé sur l'analyse de CV que vous avez effectuée (résultats : {json.dumps(context_summary)}), répondez à la question suivante de l'utilisateur. """
    
    prompt = f'{context_prompt}Question de l\'utilisateur : "{user_message}"'

    try:
        response = openai.ChatCompletion.create(
            model="gpt-3.5-turbo",
            messages=[
                {"role": "system", "content": "Vous êtes un assistant de recrutement. Répondez de manière concise et directe à la question de l'utilisateur en vous basant sur les résultats fournis."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.7,
        )
        ai_response = response.choices[0].message['content']
        return jsonify({"reply": ai_response})

    except Exception as e:
        return jsonify({"error": str(e)}), 500

def main():
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 8080)))

if __name__ == "__main__":
    main()
