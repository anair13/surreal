"""
Actor function
"""
import torch
from torch.autograd import Variable
from .base import Agent
from surreal.model.ppo_net import PPOModel, DiagGauss
import surreal.utils as U
import numpy as np
from surreal.session import ConfigError
import time

class PPOAgent(Agent):
    '''
        Class that specifies PPO agent logic
        Important attributes:
            init_log_sig: initial log sigma for diagonal gausian policy
            model: PPO_Model instance. see surreal.model.ppo_net
            pd: DiagGauss instance. see surreal.model.ppo_net
        Member functions:
            act
            reset
    '''
    def __init__(self,
                 learner_config,
                 env_config,
                 session_config,
                 agent_id,
                 agent_mode):
        super().__init__(
            learner_config=learner_config,
            env_config=env_config,
            session_config=session_config,
            agent_id=agent_id,
            agent_mode=agent_mode,
        )
        self.action_dim = self.env_config.action_spec.dim[0]
        self.obs_spec = self.env_config.obs_spec
        self.use_z_filter = self.learner_config.algo.use_z_filter
        self.init_log_sig = self.learner_config.algo.consts.init_log_sig
        self.rnn_config = self.learner_config.algo.rnn

        self.low_dim = 0
        for key in self.input_config['low_dim']:
            self.low_dim += self.obs_spec[key].shape[0]

        self.cells = None
        if self.rnn_config.if_rnn_policy:
            # Note that .detach() is necessary here to prevent overflow of memory
            # otherwise rollout in length of thousands will prevent previously
            # accumulated hidden/cell states from being freed.
            self.cells = (Variable(torch.zeros(self.rnn_config.rnn_layer, 
                                               1, # batch_size is 1
                                               self.rnn_config.rnn_hidden)).detach(),
                         Variable(torch.zeros(self.rnn_config.rnn_layer, 
                                              1, # batch_size is 1
                                              self.rnn_config.rnn_hidden)).detach())

        pixel_config = self.learner_config.algo.pixel \
                            if self.env_config.pixel_input else None
        self.model = PPOModel(
            init_log_sig=self.init_log_sig,
            obs_config=(self.obs_spec, self.learner_config.model.input),
            action_dim=self.action_dim,
            use_z_filter=self.use_z_filter,
            rnn_config=self.rnn_config,
            pixel_config=pixel_config,
            use_cuda=False,
        )

        self.pd = DiagGauss(self.action_dim)

    def act(self, obs):
        '''
            Agent returns an action based on input observation. if in training,
            returns action along with action infos, which includes the current
            probability distribution, RNN hidden states and etc.
            Args:
                obs: numpy array of (1, obs_dim)

            Returns:
                action_choice: sampled or max likelihood action to input to env
                action_info: list of auxiliary information - [onetime, persistent]
                    Note: this includes probability distribution the action is
                    sampled from, RNN hidden states
        '''
        # Note: we collect two kinds of action infos, one persistent one onetime
        # persistent info is collected for every step in rollout (i.e. policy probability distribution)
        # onetime info is collected for the first step in partial trajectory (i.e. RNN hidden state)
        # see ExpSenderWrapperMultiStepMovingWindowWithInfo in exp_sender_wrapper for more
        action_info = [[], []]

        obs_tensor = {}
        for k in obs.keys():
           tmp_tensor = U.to_float_tensor(obs[k])
           obs[k] = Variable(tmp_tensor.unsqueeze(0))
        
        if self.rnn_config.if_rnn_policy:
            action_info[0].append(self.cells[0].squeeze(1).data.numpy())
            action_info[0].append(self.cells[1].squeeze(1).data.numpy())

        action_pd, self.cells = self.model.forward_actor_expose_cells(obs, self.cells)
        action_pd = action_pd.data.numpy()

        if self.agent_mode != 'eval_deterministic':
            action_choice = self.pd.sample(action_pd)
        else:
            action_choice = self.pd.maxprob(action_pd)
        np.clip(action_choice, -1, 1, out=action_choice)
        
        action_choice = action_choice.reshape((-1,))
        action_pd     = action_pd.reshape((-1,))
        action_info[1].append(action_pd)

        if self.agent_mode != 'training':
            return action_choice
        else: 
            time.sleep(self.env_config.sleep_time)
            return action_choice, action_info

    def module_dict(self):
        return {
            'ppo': self.model,
        }

    def default_config(self):
        return {
            'model': {
                'convs': '_list_',
                'fc_hidden_sizes': '_list_',
            },
        }

    def reset(self):
        '''
            reset of LSTM hidden and cell states
        '''
        if self.rnn_config.if_rnn_policy:
            # Note that .detach() is necessary here to prevent overflow of memory
            # otherwise rollout in length of thousands will prevent previously
            # accumulated hidden/cell states from being freed.
            self.cells = (Variable(torch.zeros(self.rnn_config.rnn_layer, 
                                               1, # batch_size is 1
                                               self.rnn_config.rnn_hidden)).detach(),
                          Variable(torch.zeros(self.rnn_config.rnn_layer, 
                                               1, # batch_size is 1
                                               self.rnn_config.rnn_hidden)).detach())

