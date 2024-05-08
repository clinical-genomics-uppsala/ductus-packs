import os


class AnalysisFileClient():
    """Monitors a folder for files that can be processed by a workflow""" 

    def __init__(self, folders, logger):
        """Initializes the client with a list of folders""" 
        logger.info("__init__ AnalysisFileClient")
        if not isinstance(folders, list):
            self._folders = [folders]
        else:
            self._folders = folders
        self._logger = logger

    def touch(self, file):
        open(file, 'a').close()

    def next_ready(self):
        """Fetch next unprocessed file."""
        for folder in self._folders:
            # TODO: Add packs id to log in a generic way
            self._logger.info(f"Processing files in {folder}, {os.listdir(folder)}")
            for file in os.listdir(folder):
                file_full_path = os.path.join(folder, file)
                processed_indicator = os.path.join(folder ,f".processed.{file}")
                if os.path.isfile(file_full_path) and not file.startswith(".processed.") and file.endswith('.csv') and not os.path.exists(processed_indicator):
                    result = dict()
                    result['analysis_file'] = os.path.join(folder, file)
                    self.touch(processed_indicator)
                    return result 
        return None