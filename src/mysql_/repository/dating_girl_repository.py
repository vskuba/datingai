from py_modules.mysql_.repository.abstract_repository import AbstractRepository


class DatingGirlRepository(AbstractRepository):
    def table_name_get(self) -> str:
        return 'dating_girl'
