# MIT License
#
# Copyright (c) 2018 Evgeny Medvedev, evge.medvedev@gmail.com
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.


import logging
from builtins import map

from ethereumetl.domain.token_transfer import EthTokenTransfer
from ethereumetl.utils import chunk_string, hex_to_dec, to_normalized_address

# https://ethereum.stackexchange.com/questions/12553/understanding-logs-and-log-blooms

# Transfer(address,address,uint256) – ERC20/ERC721
TRANSFER_EVENT_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"

# TransferSingle(address,address,address,uint256,uint256) – ERC1155
# (address indexed operator, address indexed from, address indexed to, uint256 id, uint256 value)
TRANSFER_ERC1155_EVENT_TOPIC = "0xc3d58168c5ae7397731d063d5bbf3d657854427343f4c083240f7aacaa2d0f62"

# TransferBatch(address,address,address,uint256[],uint256[]) – ERC1155
# TransferBatch(address indexed operator, address indexed from, address indexed to, uint256[] ids, uint256[] values)
TRANSFER_BATCH_ERC1155_EVENT_TOPIC = "0x4a39dc06d4c0dbc64b70af90fd698a233a518aa5d07e595d983b8c0526c8f7fb"

logger = logging.getLogger(__name__)


class EthTokenTransferExtractor(object):
    def extract_transfer_from_log(self, receipt_log):
        topics = receipt_log.topics
        if topics is None or len(topics) < 1:
            # This is normal, topics can be empty for anonymous events
            return None

        if (topics[0]).casefold() == TRANSFER_EVENT_TOPIC:
            return self._handle_transfer(receipt_log, topics)

        elif (topics[0]).casefold() == TRANSFER_ERC1155_EVENT_TOPIC:
            return self._handle_erc1155_transfer(receipt_log, topics)

        elif (topics[0]).casefold() == TRANSFER_BATCH_ERC1155_EVENT_TOPIC:
            return self._handle_erc1155_batch_transfer(receipt_log, topics)

        return None

    def _handle_transfer(self, receipt_log, topics):
        # Handle un-indexed event fields
        topics_with_data = topics + split_to_words(receipt_log.data)
        # if the number of topics and fields in data part != 4, then it's a weird event
        # 4 topics are(in exact order): event signature, from, to, value
        if len(topics_with_data) != 4:
            logger.warning(
                "The number of topics and data parts is not equal to 4 in log {} of transaction {}".format(
                    receipt_log.log_index, receipt_log.transaction_hash
                )
            )
            return None

        token_transfer = EthTokenTransfer()
        token_transfer.token_address = to_normalized_address(receipt_log.address)
        token_transfer.from_address = word_to_address(topics_with_data[1])
        token_transfer.to_address = word_to_address(topics_with_data[2])
        token_transfer.value = hex_to_dec(topics_with_data[3])
        token_transfer.token_id = None
        token_transfer.transaction_hash = receipt_log.transaction_hash
        token_transfer.log_index = receipt_log.log_index
        token_transfer.block_number = receipt_log.block_number

        return [token_transfer]

    def _handle_erc1155_transfer(self, receipt_log, topics):
        # Handle un-indexed event fields
        topics_with_data = topics + split_to_words(receipt_log.data)
        # if the number of topics != 4 and with fields in data part != 6, then it's a weird event
        # 6 topics are(in exact order): event signature, operator, from, to, id, value
        if len(topics_with_data) != 6 or len(topics) != 4:
            logger.warning(
                "The number of topics and data parts is not equal to 6 in log {} of transaction {}".format(
                    receipt_log.log_index, receipt_log.transaction_hash
                )
            )
            return None

        token_transfer = EthTokenTransfer()
        token_transfer.token_address = to_normalized_address(receipt_log.address)
        token_transfer.from_address = word_to_address(topics_with_data[2])
        token_transfer.to_address = word_to_address(topics_with_data[3])
        token_transfer.token_id = hex_to_dec(topics_with_data[4])
        token_transfer.value = hex_to_dec(topics_with_data[5])
        token_transfer.transaction_hash = receipt_log.transaction_hash
        token_transfer.log_index = receipt_log.log_index
        token_transfer.block_number = receipt_log.block_number

        return [token_transfer]

    def _handle_erc1155_batch_transfer(self, receipt_log, topics):
        topics_with_data = topics + split_to_words(receipt_log.data)
        # if the number of topics != 4 then it's a weird event
        if len(topics) != 4:
            logger.warning(
                "The number of topics and data parts is not equal to 6 in log {} of transaction {}".format(
                    receipt_log.log_index, receipt_log.transaction_hash
                )
            )
            return None

        # structure of indexed fields in topics:
        # event signature, operator, from, to
        # structure of un-indexed fields in data part:
        #     id_key, value_key, id_count, id[], value_count, value[]
        #     example: 64 192 3 8 7 7 3 1 1 1
        #     from transaction with hash – 0x9ecd9463c4359a8347da8f0956f897f0b1aca58e12d6a9d9b47d4d07d724a9b2
        token_transfers = []

        ids_count = hex_to_dec(topics_with_data[6])
        values_count = hex_to_dec(topics_with_data[6 + ids_count + 1])

        if ids_count != values_count:
            logger.warning(
                "The number of ids and values is not equal in log {} of transaction {}".format(
                    receipt_log.log_index, receipt_log.transaction_hash
                )
            )
            return None

        for i in range(0, ids_count):
            # 6: The offset to the beginning of the ids array in the topics_with_data list. The first 6 elements are event signature, operator, from, to, id_key, and value_key.
            # 1: The element after the ids array, which is the ids_count.
            # i: The current index in the range of ids_count. This value increments with each iteration.
            token_id = hex_to_dec(topics_with_data[6 + 1 + i])
            # 6: The offset to the beginning of the ids array in the topics_with_data list. The first 6 elements are event signature, operator, from, to, id_key, and value_key.
            # ids_count: The number of ids in the ids array.
            # 1: The element after the ids array, which is the value_count.
            # i: The current index in the range of ids_count. This value increments with each iteration.
            # 1: The offset to the beginning of the values array, which is right after the value_count.
            value = hex_to_dec(topics_with_data[6 + ids_count + 1 + i + 1])

            token_transfer = EthTokenTransfer()
            token_transfer.token_address = to_normalized_address(receipt_log.address)
            token_transfer.from_address = word_to_address(topics_with_data[2])
            token_transfer.to_address = word_to_address(topics_with_data[3])
            token_transfer.token_id = token_id
            token_transfer.value = value
            token_transfer.transaction_hash = receipt_log.transaction_hash
            token_transfer.log_index = receipt_log.log_index
            token_transfer.block_number = receipt_log.block_number
            token_transfers.append(token_transfer)

        return token_transfers


def split_to_words(data):
    if data and len(data) > 2:
        data_without_0x = data[2:]
        words = list(chunk_string(data_without_0x, 64))
        words_with_0x = list(map(lambda word: "0x" + word, words))
        return words_with_0x
    return []


def word_to_address(param):
    if param is None:
        return None
    elif len(param) >= 40:
        return to_normalized_address("0x" + param[-40:])
    else:
        return to_normalized_address(param)
