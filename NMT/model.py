import torch
import torch.nn as nn
import torch.nn.functional as F
from torch.autograd import Variable
import numpy as np
import math

class NMT(nn.Module):
    def __init__(self, src_vocab_size, trg_vocab_size, word_emb_size, 
            hidden_size, src_vocab, trg_vocab, attn_model = 'general', use_cuda = False):
        super(NMT, self).__init__()

        self.src_vocab_size = src_vocab_size
        self.trg_vocab_size = trg_vocab_size
        self.word_emb_size = word_emb_size
        self.hidden_size = hidden_size
        self.attn_model = attn_model
        self.src_vocab = src_vocab
        self.trg_vocab = trg_vocab
        self.use_cuda = use_cuda

        # Initialize models
        self.encoder = EncoderRNN(src_vocab_size, word_emb_size, hidden_size) 
        self.decoder = LuongAttnDecoderRNN(attn_model, trg_vocab_size, word_emb_size,hidden_size, trg_vocab_size)

        if use_cuda > 0:
            self.encoder.cuda()
            self.decoder.cuda()
        else:
            self.encoder.cpu()
            self.decoder.cpu()

        self.reset_parameters()

    def reset_parameters(self):
        for weight in self.parameters():
          weight.data.uniform_(-0.1, 0.1)

    def forward(self, src_batch, trg_batch, is_train):
        # Encoding
        encoder_outputs, (e_h,e_c) = self.encoder(src_batch)

        e_h_ = torch.cat([e_h[0:e_h.size(0):2], e_h[1:e_h.size(0):2]], 2)
        e_c_ = torch.cat([e_c[0:e_c.size(0):2], e_c[1:e_c.size(0):2]], 2)


        # Preparing for decoding
        trg_seq_len = trg_batch.size(0)
        batch_size = trg_batch.size(1)
        sys_out_batch = Variable(torch.FloatTensor(trg_seq_len, batch_size, self.trg_vocab_size).fill_(self.trg_vocab.stoi['<blank>'])) # (trg_seq_len, batch_size, trg_vocab_size)
        decoder_input = Variable(torch.LongTensor([self.trg_vocab.stoi['<s>']] * batch_size))

        # # Use last (forward) hidden state from encoder (Luong's paper)
        d_h = e_h_.squeeze(0)
        d_c = e_c_.squeeze(0)
        if self.use_cuda > 0:
            decoder_input = decoder_input.cuda()
            sys_out_batch = sys_out_batch.cuda()

        hidden_size = e_h_.size(2)
        if self.use_cuda:
            attn_vector = Variable(torch.zeros(batch_size, hidden_size), requires_grad=False).cuda()
        else:
            attn_vector = Variable(torch.zeros(batch_size, hidden_size), requires_grad=False)

        # Decoding
        for d_idx in range(trg_seq_len):
            d_h = d_h.detach()  # detach hidden variable
            d_c = d_c.detach()  #detach cell state
            decoder_output, (d_h, d_c), attn_vector = self.decoder(decoder_input, (d_h, d_c), encoder_outputs, attn_vector)
            sys_out_batch[d_idx] = decoder_output
            attn_vector = attn_vector.view(batch_size, hidden_size)
            if is_train:
                decoder_input = trg_batch[d_idx]
            else:
                top_val, top_inx = decoder_output.view(batch_size, -1).topk(1)
                decoder_input = top_inx.squeeze(1)

        return sys_out_batch


# Encoder Module
class EncoderRNN(nn.Module):
    def __init__(self, input_vocab_size, embed_size, hidden_size, num_direction=2, dropout=0.2, n_layers=1):
        super(EncoderRNN, self).__init__()

        self.input_vocab_size = input_vocab_size
        self.embed_size = embed_size
        self.hidden_size = hidden_size // num_direction
        self.n_layers = n_layers
        
        self.embedding = nn.Embedding(input_vocab_size, embed_size)
        self.lstm = nn.LSTM(embed_size, self.hidden_size, n_layers, dropout=dropout, bidirectional=True)
        
    def forward(self, input_seqs_batch):

        batch_size = input_seqs_batch.size(1)
        
        embedded = self.embedding(input_seqs_batch)

        e_outputs, (e_h, e_c) = self.lstm(embedded)

        # Sum bidirectional outputs
        # e_outputs = e_outputs[:, :, :self.hidden_size] + e_outputs[:, :, self.hidden_size:]

        return e_outputs, (e_h, e_c)


# Attention Module
class Attn(nn.Module):
    def __init__(self, method, hidden_size):
        super(Attn, self).__init__()
        self.method = method
        self.hidden_size = hidden_size
        
        if self.method == 'general':
            self.attn = nn.Linear(self.hidden_size, hidden_size, bias=False)

        else:
            raise NotImplementedError


    def forward(self, hidden, encoder_outputs):
        align = self.score(hidden, encoder_outputs)

        batch, targetL, sourceL = align.size()

        # Normalize energies to weights in range 0 to 1
        # align_vectors = F.softmax(align.view(batch * targetL, sourceL), dim=1)
        align_vectors = F.softmax(align.view(batch * targetL, sourceL),dim=1)

        align_vectors = align_vectors.view(batch, targetL, sourceL)

        return align_vectors
    
    def score(self, hidden, encoder_output):

        src_len, src_batch, src_dim = encoder_output.size()
        tgt_len, tgt_batch, tgt_dim = hidden.size()

        if self.method == 'general':
            h_t_ = hidden.view(tgt_batch*tgt_len, tgt_dim)
            h_t_ = self.attn(h_t_)
            h_t = h_t_.view(tgt_batch, tgt_len, tgt_dim)
            h_s_ = encoder_output.transpose(0, 1)
            h_s_ = h_s_.transpose(1, 2)
            # (batch, t_len, d) x (batch, d, s_len) --> (batch, t_len, s_len)
            return torch.bmm(h_t, h_s_)

        else:
            raise NotImplementedError

# Luong Attention Decoder Module
class LuongAttnDecoderRNN(nn.Module):
    def __init__(self, attn_model, input_vocab_size, embed_size, hidden_size, output_size, dropout=0.2, n_layers=1):
        super(LuongAttnDecoderRNN, self).__init__()

        # Keep for reference
        self.attn_model = attn_model
        self.input_vocab_size = input_vocab_size
        self.hidden_size = hidden_size
        self.embed_size = embed_size
        self.n_layers = n_layers
        self.dropout = nn.Dropout(dropout)

        # Define layers
        self.embedding = nn.Embedding(input_vocab_size, embed_size)
        self.lstm = nn.LSTMCell(embed_size + hidden_size, hidden_size)
        self.concat = nn.Linear(hidden_size * 2, hidden_size)
        self.out = nn.Linear(hidden_size, output_size)
        
        # instantiate attention class
        if attn_model != None:
            self.attn = Attn(attn_model, hidden_size)

    def forward(self, input_seq, last_hidden, encoder_outputs, attn_vector):
        # Note: we run this one step at a time

        # Get the embedding of the current input word (last output word)
        batch_size = input_seq.size(0)
        embedded = self.embedding(input_seq)
        #embedded = embedded.view(1, batch_size, self.embed_size) # S=1 x B x N
        emb_t = torch.cat([embedded, attn_vector], 1)
        # Get current hidden state from input word and last hidden state
        d_h, d_c = self.lstm(emb_t, last_hidden) # rnn_output: 1*batch*hidden

        # Calculate attention from current RNN state and all encoder outputs;
        # apply to encoder outputs to get weighted average
        decoder_hidden = d_h.view(1, batch_size, -1)
        alignment_vector = self.attn(decoder_hidden, encoder_outputs) # batch * hidden
        
        # encoder_outputs = encoder_outputs.transpose(0, 1)
        context = torch.bmm(alignment_vector, encoder_outputs.permute(1,0,2))
        c_t = context.squeeze(1)

        # Attentional vector using the RNN hidden state and context vector
        # concatenated together (Luong eq. 5)
        concat_c = torch.cat((c_t, d_h), 1)
        attn_vector = F.tanh(self.concat(concat_c))

        decoder_out = self.dropout(attn_vector)

        # Finally predict next token (Luong eq. 6)
        # output = nn.functional.log_softmax(self.out(decoder_out),dim=1)
        output = nn.functional.log_softmax(self.out(decoder_out), dim=1)

        # Return final output, hidden state
        return output, (d_h, d_c), attn_vector
