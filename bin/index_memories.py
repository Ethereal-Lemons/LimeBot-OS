import asyncio 
import os 
import sys 
from pathlib import Path 


sys .path .append (os .path .dirname (os .path .dirname (os .path .abspath (__file__ ))))

from loguru import logger 
from core .vectors import get_vector_service 
from config import load_config 

async def index_all ():
    logger .info ("Initializing Vector Memory Indexing...")
    config =load_config ()
    vector_service =get_vector_service (config )

    base_dir =Path (__file__ ).resolve ().parent .parent 
    persona_dir =base_dir /"persona"
    memory_dir =persona_dir /"memory"
    long_term_file =persona_dir /"MEMORY.md"


    if long_term_file .exists ():
        logger .info (f"Indexing {long_term_file .name }...")
        content =long_term_file .read_text (encoding ="utf-8")
        if content .strip ():
            await vector_service .add_entry (content ,category ="long_term")


    if memory_dir .exists ():
        files =list (memory_dir .glob ("*.md"))
        logger .info (f"Found {len (files )} daily logs to index...")
        for f in sorted (files ):
            logger .debug (f"  - Indexing {f .name }...")
            content =f .read_text (encoding ="utf-8")
            if content .strip ():
                entries =content .split ("\n- **")
                for entry in entries :
                    if entry .strip ():
                        clean_entry =entry if entry .startswith ("- **")else f"- **{entry }"
                        await vector_service .add_entry (clean_entry ,category ="journal")

    logger .success ("All existing memories have been indexed semantically!")

if __name__ =="__main__":
    asyncio .run (index_all ())
