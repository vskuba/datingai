from py_modules.mysql_.repository.abstract_repository import AbstractRepository


class DatingProfileRepository(AbstractRepository):
    def table_name_get(self) -> str:
        return 'dating_profile'
