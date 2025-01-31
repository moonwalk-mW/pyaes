'''*****************************************************************************
* Filename: blockfeeder.py
* Date: 9/1/2022
* 
* Extends pyaes to implement Ciphertext Stealing mode for CBC & ECB Block modes
* https://github.com/ricmoo/pyaes
* Refer to NIST SP 800-38A for specification information.
* https://nvlpubs.nist.gov/nistpubs/Legacy/SP/nistspecialpublication800-38a-add.pdf
*
* Currently only works with python 3
* @TODO python 2 compatibility
****************************************************************************'''


# The MIT License (MIT)
#
# Copyright (c) 2014 Richard Moore
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in
# all copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
# THE SOFTWARE.


from .aes import AESBlockModeOfOperation, AESSegmentModeOfOperation, AESStreamModeOfOperation
from .util import append_PKCS7_padding, strip_PKCS7_padding, to_bufferable


# First we inject three functions to each of the modes of operations
#
#    _can_consume(size)
#       - Given a size, determine how many bytes could be consumed in
#         a single call to either the decrypt or encrypt method
#
#    _final_encrypt(data, padding = PADDING_DEFAULT)
#       - call and return encrypt on this (last) chunk of data,
#         padding as necessary; this will always be at least 16
#         bytes unless the total incoming input was less than 16
#         bytes
#
#    _final_decrypt(data, padding = PADDING_DEFAULT)
#       - same as _final_encrypt except for decrypt, for
#         stripping off padding
#

PADDING_NONE       = 'none'
PADDING_DEFAULT    = 'default'
PADDING_CS1        = 'CS1'
PADDING_CS2        = 'CS2'
PADDING_CS3        = 'CS3'

# @TODO: explicit PKCS#7
# PADDING_PKCS7

# ECB and CBC are block-only ciphers

def _block_can_consume(self, size):
    if size >= 16: return 16
    return 0

#for CS2 and CS3 modes, swap the last two blocks of data
def _CS_encrypt_swap_blocks(self, data):
    return data[16:] + data[:16]

#for CS2 and CS3 modes, swap the last two blocks of data    
def _CS_decrypt_swap_blocks(self, data):
    d = len(data) - 16 # "minus" vs "mod" b/c we handle case where the last block is a full block, not a partial block
    return data[d:] + data[:d]

#encrypt a message of variable length using Ciphertext Stealing Mode #1
def _CS1_encrypt(self, data):
    d = len(data) % 16 #calculate length of final block
    if d != 0: #last block is a partial block
        Cn_1 = self.encrypt(data[:16]) #encrypt penultimate block
        if type(self).__name__ == "AESModeOfOperationCBC": #handle special case for CBC mode
            Pn = data[16:] + bytes('\0'*(16-d), 'utf-8') #works py3 #pad final block with Zeros
                                                         #when encrypter XORs next block, it "pads" with LSBb-d(Cn-1)
                                                         #aka the ciphertext from penultimate block we steal for padding
                                                         #We have to do it this way because the encrypter in aes.py saves state
        else:#ECB mode, just copy the encrypted bytes from penultimate block Cn_1
            Pn = data[16:] + Cn_1[d:]
        Cn = self.encrypt(Pn) #encrypt final block
        return Cn_1[:d] + Cn #return shortened Cn_1 block (less the stolen bytes) and the final block Cn
    return self.encrypt(data) #data is a multiple of 16 (block length), just call Encrypt and be done

#decrypt a message of variable length using Ciphertext Stealing Mode #1
def _CS1_decrypt(self, data):
    d = len(data) % 16#calculate length of final block
    if d != 0: #last block is a partial block
        Cn = self._aes.decrypt((data[len(data)-16:])) #call InvCipher on the final block. do not use the Decrypter
                                                      #because decrypter saves state, which we don't want here
        Cn_1 = data[:d] + bytes(Cn[d:]) #recover the stolen ciphertext and concat to penultimate block Cn_1
        Pn_1 = self.decrypt(Cn_1) #decrypt penultimate block
        if type(self).__name__ == "AESModeOfOperationCBC": #handle CBC mode
            Pn = [ (p ^ l) for (p, l) in zip(bytes(Cn[:d]), Cn_1[:d]) ] #do our XOR to finalize the decrypt of final block
            return Pn_1 + bytes(Pn) #return our final two decrypted blocks (CBC mode)
        return Pn_1 + bytes(Cn[:d]) #return our final two decrypted blocks (ECB mode)
    return self.decrypt(data[:16]) #data is a multiple of 16 (block length) just call decrypt and be done

#encrypt a message of variable length using Ciphertext Stealing Mode #2
#if the final block is a full block, this is equivalent to CS Mode #1
#otherwise we swap the order of the final two blocks
def _CS2_encrypt(self, data):
    d = len(data) % 16
    c = _CS1_encrypt(self,data)
    if d != 0:
      return _CS_encrypt_swap_blocks(self, c)
    return c
 
#Decrypt a message of variable length using Ciphertext Stealing Mode #2
#if the final block is a full block, this is equivalent to CS Mode #1
#otherwise we swap the order of the final two blocks  
def _CS2_decrypt(self, data):
    d = len(data) % 16
    if d == 0:
        return _CS1_decrypt(self,data)
    swapped = _CS_decrypt_swap_blocks(self,data)
    return _CS1_decrypt(self, swapped)

#Encrypt a message of variable length using Ciphertext Stealing Mode #3
#We ALWAYS swap the order of the final two blocks regardless if the last block is a full or partial block
def _CS3_encrypt(self, data):
    if len(data) == 32: #need to handle case where we have 2 full blocks remaining b/c unconditional swap
        c = _CS1_encrypt(self, data[:16]) + _CS1_encrypt(self, data[16:])
    else:
        c = _CS1_encrypt(self,data) #handles 16 <= len(data) < 32
    return _CS_encrypt_swap_blocks(self, c)

#Decrypt a message of variable length using Ciphertext Stealing Mode #3
#We ALWAYS swap the order of the final two blocks regardless if the last block is a full or partial block   
def _CS3_decrypt(self, data):
    c = _CS_decrypt_swap_blocks(self, data)
    if len(data) == 32: #need to handle case where we have 2 full blocks remaining b/c unconditional swap
        return _CS1_decrypt(self, c[:16]) + _CS1_decrypt(self, c[16:])
    else:
        return _CS1_decrypt(self, c) #handles 16 <= len(data) < 32

# After padding, we may have more than one block
def _block_final_encrypt(self, data, padding = PADDING_DEFAULT):
    if padding == PADDING_DEFAULT:
        data = append_PKCS7_padding(data)

    elif padding == PADDING_NONE:
        if len(data) != 16:
            raise Exception('invalid data length for final block')
        
    elif padding == PADDING_CS1:
        if type(self).__name__ == "AESModeOfOperationECB" or "AESModeOfOperationCBC":
            return _CS1_encrypt(self, data)
        raise Exception('invalid padding option - Ciphertext stealing can only be used with CBC and ECB modes')    
  
    elif padding == PADDING_CS2:
        if type(self).__name__ == "AESModeOfOperationECB" or "AESModeOfOperationCBC":
            return _CS2_encrypt(self, data)
        raise Exception('invalid padding option - Ciphertext stealing can only be used with CBC and ECB modes')       
  
    elif padding == PADDING_CS3:
        if type(self).__name__ == "AESModeOfOperationECB" or "AESModeOfOperationCBC":
            return _CS3_encrypt(self, data)
        raise Exception('invalid padding option - Ciphertext stealing can only be used with CBC and ECB modes')    
  
    else:
        raise Exception('invalid padding option')

    if len(data) == 32:
        return self.encrypt(data[:16]) + self.encrypt(data[16:])

    return self.encrypt(data)


def _block_final_decrypt(self, data, padding = PADDING_DEFAULT):
    if padding == PADDING_DEFAULT:
        return strip_PKCS7_padding(self.decrypt(data))

    if padding == PADDING_CS1:
        if type(self).__name__ == "AESModeOfOperationECB" or "AESModeOfOperationCBC":
            return _CS1_decrypt(self, data)
        raise Exception('invalid padding option - Ciphertext stealing can only be used with CBC and ECB modes')       
    elif padding == PADDING_CS2:
        if type(self).__name__ == "AESModeOfOperationECB" or "AESModeOfOperationCBC":
            return _CS2_decrypt(self, data)
        raise Exception('invalid padding option - Ciphertext stealing can only be used with CBC and ECB modes')    
    elif padding == PADDING_CS3:
        if type(self).__name__ == "AESModeOfOperationECB" or "AESModeOfOperationCBC":
            return _CS3_decrypt(self, data)
        raise Exception('invalid padding option - Ciphertext stealing can only be used with CBC and ECB modes')    
            
    if padding == PADDING_NONE:
        if len(data) != 16:
            raise Exception('invalid data length for final block')
        return self.decrypt(data)

    raise Exception('invalid padding option')

AESBlockModeOfOperation._can_consume = _block_can_consume
AESBlockModeOfOperation._final_encrypt = _block_final_encrypt
AESBlockModeOfOperation._final_decrypt = _block_final_decrypt



# CFB is a segment cipher

def _segment_can_consume(self, size):
    return self.segment_bytes * int(size // self.segment_bytes)

# CFB can handle a non-segment-sized block at the end using the remaining cipherblock
def _segment_final_encrypt(self, data, padding = PADDING_DEFAULT):
    if padding != PADDING_DEFAULT:
        raise Exception('invalid padding option')

    faux_padding = (chr(0) * (self.segment_bytes - (len(data) % self.segment_bytes)))
    padded = data + to_bufferable(faux_padding)
    return self.encrypt(padded)[:len(data)]

# CFB can handle a non-segment-sized block at the end using the remaining cipherblock
def _segment_final_decrypt(self, data, padding = PADDING_DEFAULT):
    if padding != PADDING_DEFAULT:
        raise Exception('invalid padding option')

    faux_padding = (chr(0) * (self.segment_bytes - (len(data) % self.segment_bytes)))
    padded = data + to_bufferable(faux_padding)
    return self.decrypt(padded)[:len(data)]

AESSegmentModeOfOperation._can_consume = _segment_can_consume
AESSegmentModeOfOperation._final_encrypt = _segment_final_encrypt
AESSegmentModeOfOperation._final_decrypt = _segment_final_decrypt



# OFB and CTR are stream ciphers

def _stream_can_consume(self, size):
    return size

def _stream_final_encrypt(self, data, padding = PADDING_DEFAULT):
    if padding not in [PADDING_NONE, PADDING_DEFAULT]:
        raise Exception('invalid padding option')

    return self.encrypt(data)

def _stream_final_decrypt(self, data, padding = PADDING_DEFAULT):
    if padding not in [PADDING_NONE, PADDING_DEFAULT]:
        raise Exception('invalid padding option')

    return self.decrypt(data)

AESStreamModeOfOperation._can_consume = _stream_can_consume
AESStreamModeOfOperation._final_encrypt = _stream_final_encrypt
AESStreamModeOfOperation._final_decrypt = _stream_final_decrypt



class BlockFeeder(object):
    '''The super-class for objects to handle chunking a stream of bytes
       into the appropriate block size for the underlying mode of operation
       and applying (or stripping) padding, as necessary.'''

    def __init__(self, mode, feed, final, padding = PADDING_DEFAULT):
        self._mode = mode
        self._feed = feed
        self._final = final
        self._buffer = to_bufferable("")
        self._padding = padding

    def feed(self, data = None):
        '''Provide bytes to encrypt (or decrypt), returning any bytes
           possible from this or any previous calls to feed.

           Call with None or an empty string to flush the mode of
           operation and return any final bytes; no further calls to
           feed may be made.'''

        if self._buffer is None:
            raise ValueError('already finished feeder')

        # Finalize; process the spare bytes we were keeping
        if data is None:
            result = self._final(self._buffer, self._padding)
            self._buffer = None
            return result

        self._buffer += to_bufferable(data)

        # We keep 16 bytes around so we can determine padding
        result = to_bufferable('')
        while len(self._buffer) > 16:
            #if we're ciphertext stealing in CS3 mode we need to check the edge case of only 32 bytes remaining
            #because the last 2 blocks are unconditionally swapped
            if ((len(self._buffer) == 32) and (self._padding == PADDING_CS3)):
                can_consume = 0
            else:
                can_consume = self._mode._can_consume(len(self._buffer) - 16)
                
            if can_consume == 0: break
            result += self._feed(self._buffer[:can_consume])
            self._buffer = self._buffer[can_consume:]

        return result


class Encrypter(BlockFeeder):
    'Accepts bytes of plaintext and returns encrypted ciphertext.'

    def __init__(self, mode, padding = PADDING_DEFAULT):
        BlockFeeder.__init__(self, mode, mode.encrypt, mode._final_encrypt, padding)


class Decrypter(BlockFeeder):
    'Accepts bytes of ciphertext and returns decrypted plaintext.'

    def __init__(self, mode, padding = PADDING_DEFAULT):
        BlockFeeder.__init__(self, mode, mode.decrypt, mode._final_decrypt, padding)


# 8kb blocks
BLOCK_SIZE = (1 << 13)

def _feed_stream(feeder, in_stream, out_stream, block_size = BLOCK_SIZE):
    'Uses feeder to read and convert from in_stream and write to out_stream.'

    while True:
        chunk = in_stream.read(block_size)
        if not chunk:
            break
        converted = feeder.feed(chunk)
        out_stream.write(converted)
    converted = feeder.feed()
    out_stream.write(converted)


def encrypt_stream(mode, in_stream, out_stream, block_size = BLOCK_SIZE, padding = PADDING_DEFAULT):
    'Encrypts a stream of bytes from in_stream to out_stream using mode.'

    encrypter = Encrypter(mode, padding = padding)
    _feed_stream(encrypter, in_stream, out_stream, block_size)


def decrypt_stream(mode, in_stream, out_stream, block_size = BLOCK_SIZE, padding = PADDING_DEFAULT):
    'Decrypts a stream of bytes from in_stream to out_stream using mode.'

    decrypter = Decrypter(mode, padding = padding)
    _feed_stream(decrypter, in_stream, out_stream, block_size)
