import aiohttp
import logging
from schema.whisper_schema import WhisperServiceResponse

# Configure logging
logging.basicConfig(level=logging.DEBUG)


class WhisperAPI:
    def __init__(self, transcribe_url: str):
        self.transcribe_url = transcribe_url

    async def transcribe(self, audio: bytes, timeout: int = 300) -> str | None:
        url = f"{self.transcribe_url}"

        async with aiohttp.ClientSession() as session:
            data = aiohttp.FormData()
            data.add_field('audio', audio, filename='audio.oga',
                           content_type='audio/ogg')

            try:
                logging.debug(
                    f"Sending transcription request to Whisper backend at {url}")

                async with session.post(url, data=data, timeout=timeout) as response:
                    logging.debug(f"Received response with status: {response.status}")

                    if response.status != 200:
                        logging.error(
                            f"Request failed with status code {response.status}")
                        return None

                    # Check and log the response content
                    response_text = await response.text()
                    logging.debug(f"Raw response text: {response_text}")
                    whisper_response_json = await response.json(content_type=None)
                    logging.debug(f"Parsed JSON response: {whisper_response_json}")

                    # Process JSON response as before
                    whisper_response = WhisperServiceResponse(**whisper_response_json)
                    if whisper_response.status == "error":
                        logging.error("Whisper API returned an error in the response.")
                        return None

                    logging.info("Transcription successful.")
                    return whisper_response.response

            except aiohttp.ClientError as e:
                logging.error(f"Failed to connect to Whisper backend: {e}")
                return None
            except aiohttp.ContentTypeError:
                logging.error("Failed to parse JSON response from Whisper backend.")
                return None
