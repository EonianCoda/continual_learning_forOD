# built-in
import collections
import os
import pickle
# torch
from torchvision import transforms
from torch.utils.data.dataloader import DataLoader
# retinanet
from retinanet.dataloader import AspectRatioBasedSampler, IL_dataset, Replay_dataset, Resizer, Augmenter, Normalizer, collater
from retinanet.model import create_retinanet
# traing util
from preprocessing.params import Params
from preprocessing.debug import debug_print
# IL
from IL_method.mas import MAS
from IL_method.agem import A_GEM


class IL_Trainer(object):
    def __init__(self, params:Params, model, optimizer, scheduler, dataset_train:IL_dataset):
        self.params = params
 
        # training setting
        self.model = model
        self.optimizer = optimizer
        self.scheduler = scheduler
        self.dataset_train = dataset_train
        self.loss_hist = collections.deque(maxlen=500)
        self.dataloader_train = None
        self.update_dataloader()
            
        
        self.cur_state = self.params['start_state']
        
        # incremental tools
        self.prev_model = None
        self.dataset_replay = None
        self.mas = None
        self.agem = None

        # if start state is not initial state, then update incremental learning setting
        if self.cur_state > 1:
            self.init_agem()
            self.init_replay_dataset()
            self.update_prev_model()
            self.update_mas()

    def update_dataloader(self):
        if self.dataloader_train != None:
            del self.dataloader_train
        sampler = AspectRatioBasedSampler(self.dataset_train, batch_size = self.params['batch_size'], drop_last=False)
        self.dataloader_train = DataLoader(self.dataset_train, num_workers=2, collate_fn=collater, batch_sampler=sampler)

    def update_prev_model(self):
        """update previous model, if distill = True
        """
        if self.cur_state == 0:
            raise ValueError("Initial state doesn't have previous state")
        if not self.params['distill']:
            return

        if self.prev_model != None:
            self.prev_model.cpu()
            del self.prev_model
        self.prev_model = create_retinanet(self.params['depth'], num_classes=self.params.states[self.cur_state - 1]['num_knowing_class'])
        self.params.load_model(self.cur_state - 1, -1, self.prev_model)
        self.prev_model.training = False
        self.prev_model.cuda()

    def init_replay_dataset(self):
        # Replay dataloader
        if self.params['sample_num'] <= 0:
            return
        
        self.dataset_replay = Replay_dataset(self.params,
                                            transform=transforms.Compose([Normalizer(), Augmenter(), Resizer()]))
        custom_ids = []
        # custom sample
        if self.params['sample_method'] == 'custom':
            # sample 2
            custom_ids_2 = [2008002080, 2008001302, 2010004059, 2010001043, 2009004340, 2008004603, 
                            2009004871, 2009004383, 2010004848, 2011000233, 2009001541, 2008007629, 
                            2008002850, 2008008616, 2010004660, 2010002870, 2008006004, 2009005057, 
                            2011002818, 2010003078, 2009001751, 2010003929, 2009005037, 2009005177, 
                            2008008521, 2008008121, 2010000484, 2008001479, 2010004247, 2009001147]
            # sample 1
            custom_ids_1 = [2008001302, 2010004059, 2008004603, 2009004871, 2010004848, 
                            2011002114, 2008008616, 2010002870, 2009005057, 2010003078, 
                            2008006923, 2009001948, 2009003510, 2008001479, 2010004247]
            if self.params['sample_num'] == 1:
                custom_ids = custom_ids_1
            elif self.params['sample_num'] == 2:
                custom_ids = custom_ids_2
            else:
                raise ValueError("The per num for custom sample method cannot be {}".format(self.params['sample_num']))

            self.dataset_replay.reset_by_imgIds(per_num=self.params['sample_num'], img_ids=custom_ids)
 
    def init_agem(self):
        if not self.params['agem']:
            self.agem = None
            return
        self.agem = A_GEM(self.model, self.dataset_replay, self.params)

    def update_mas(self):
        # set MAS penalty
        if not self.params['mas']:
            return

        debug_print("Update MAS")
        if self.mas != None:
            del self.mas
        self.mas = MAS(self.model, self.params)
        # Test if the mas file exists
        mas_file = os.path.join(self.params['ckp_path'], "state{}".format(self.cur_state - 1), "{}.pickle".format(self.params['mas_file']))
        if not self.mas.load_importance(mas_file):
            self.mas.calculate_importance(self.dataloader_train)

    def next_state(self):
        self.cur_state += 1
        self.update_mas()
        self.model.next_state(self.get_cur_state()['num_new_class'])
        self.dataset_train.next_state()
        
        if self.dataset_replay !=None:
            if self.cur_state == 1:
                self.init_replay_dataset()
            else:
                self.dataset_replay.next_state()
        if self.cur_state == 1:
            self.init_agem()
        
        self.update_dataloader()
        self.update_prev_model(self.cur_state - 1)

    def warm_up(self, epoch:int):
        # No warm-up
        if self.params['warm_stage'] == 0:
            self.warm_status = 0
            return 0

        idx, white_list = self.params.is_warmup(epoch)
        if white_list != None:
            self.model.freeze_layers(white_list)
        else:
            self.model.unfreeze_layers()
        return idx

    def save_ckp(self, epoch_loss:list,epoch:int):

        self.params.save_checkpoint(self.cur_state, epoch, self.model, self.optimizer, self.sceduler, self.loss_hist, epoch_loss)
    def get_cur_state(self):
        return self.params.states[self.cur_state]


        
