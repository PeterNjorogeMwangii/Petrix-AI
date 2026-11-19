import io,re,warnings
from pathlib import PurePosixPath
from fastapi import HTTPException
from PIL import Image,ImageOps,UnidentifiedImageError
from app.agent_knowledge.extract import extract,ImportFailure

MAX_FILE=10*1024*1024
EXTENSIONS={'.pdf','.txt','.md','.csv','.json','.docx','.png','.jpg','.jpeg','.gif','.webp'}
MIMES={'.pdf':'application/pdf','.txt':'text/plain','.md':'text/markdown','.csv':'text/csv','.json':'application/json','.docx':'application/vnd.openxmlformats-officedocument.wordprocessingml.document'}

def prepare(filename,data):
    name=PurePosixPath((filename or 'attachment').replace('\\','/')).name
    name=re.sub(r'[\x00-\x1f\x7f]','',name).strip()[:200] or 'attachment'
    extension=PurePosixPath(name.lower()).suffix
    if not data or len(data)>MAX_FILE:raise HTTPException(422,'Choose a non-empty file up to 10 MiB.')
    if extension not in EXTENSIONS:raise HTTPException(422,'Attach PDF, DOCX, TXT, Markdown, CSV, JSON, PNG, JPEG, GIF or WebP files.')
    if extension in {'.png','.jpg','.jpeg','.gif','.webp'}:
        try:
            with warnings.catch_warnings():
                warnings.simplefilter('error',Image.DecompressionBombWarning)
                with Image.open(io.BytesIO(data)) as probe:
                    if probe.format not in {'PNG','JPEG','GIF','WEBP'} or probe.width*probe.height>20_000_000:raise ValueError
                    mime=Image.MIME[probe.format];probe.verify()
                with Image.open(io.BytesIO(data)) as original:
                    original.seek(0);image=ImageOps.exif_transpose(original).convert('RGB');image.thumbnail((1600,1600));output=io.BytesIO();image.save(output,format='JPEG',quality=85);preview=output.getvalue()
            return {'filename':name,'data':data,'size':len(data),'mime_type':mime,'preview':preview,'extracted_text':'','note':'Image attached. Image understanding requires a vision-enabled Advisor model.'}
        except (OSError,ValueError,UnidentifiedImageError,Image.DecompressionBombWarning,Image.DecompressionBombError):raise HTTPException(422,'Choose a valid image up to 20 megapixels.') from None
    try:text=extract(data,name)
    except ImportFailure as error:
        message='This document has no readable text. Upload a text-based PDF or add a written description.' if error.code=='no_text' else 'The document could not be read. Use a readable, unencrypted PDF, DOCX or UTF-8/UTF-16 text file within the file limits.'
        raise HTTPException(422,message) from None
    return {'filename':name,'data':data,'size':len(data),'mime_type':MIMES[extension],'preview':None,'extracted_text':text,'note':'Readable document text attached.'}
