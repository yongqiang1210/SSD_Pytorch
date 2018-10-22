# -*- coding: utf-8 -*-
# Written by yq_yao

import torch
import torch.nn as nn
import torch.nn.functional as F
from torch.autograd import Variable
import torch.nn.init as init


def xavier(param):
    init.xavier_uniform_(param)


# def weights_init(m):
#     if isinstance(m, nn.Conv2d):
#         xavier(m.weight.data)
#         m.bias.data.zero_()


def weights_init(m):
    for key in m.state_dict():
        if key.split('.')[-1] == 'weight':
            if 'conv' in key:
                init.kaiming_normal(m.state_dict()[key], mode='fan_out')
            if 'bn' in key:
                m.state_dict()[key][...] = 1
        elif key.split('.')[-1] == 'bias':
            m.state_dict()[key][...] = 0


def trans_layers(block, fpn_num):
    layers = list()
    for i in range(fpn_num):
        layers += [
            nn.Sequential(
                nn.Conv2d(block[i], 256, kernel_size=3, stride=1, padding=1),
                nn.ReLU(inplace=True),
                nn.Conv2d(256, 256, kernel_size=3, stride=1, padding=1))
        ]

    return layers


def latent_layers(fpn_num):
    layers = []
    for i in range(fpn_num):
        layers += [nn.Conv2d(256, 256, kernel_size=3, stride=1, padding=1)]
    return layers


def up_layers(fpn_num):
    layers = []
    for i in range(fpn_num - 1):
        layers += [nn.Upsample(scale_factor=2, mode='bilinear')]
    return layers


class FpnAdapter(nn.Module):
    def __init__(self, block, fpn_num):
        super(FpnAdapter, self).__init__()
        self.trans_layers = nn.ModuleList(trans_layers(block, fpn_num))
        self.up_layers = nn.ModuleList(up_layers(fpn_num))
        self.latent_layers = nn.ModuleList(latent_layers(fpn_num))
        self._init_modules()

    def _init_modules(self):
        self.trans_layers.apply(weights_init)
        self.latent_layers.apply(weights_init)

    def forward(self, x):
        trans_layers_list = list()
        fpn_out = list()
        for (p, t) in zip(x, self.trans_layers):
            trans_layers_list.append(t(p))
        last = F.relu(
            self.latent_layers[-1](trans_layers_list[-1]), inplace=True)
        # last layer
        fpn_out.append(last)
        _up = self.up_layers[-1](last)
        for i in range(len(trans_layers_list) - 2, -1, -1):
            q = F.relu(trans_layers_list[i] + _up, inplace=True)
            q = F.relu(self.latent_layers[i](q), inplace=True)
            fpn_out.append(q)
            if i > 0:
                _up = self.up_layers[i - 1](q)
        fpn_out = fpn_out[::-1]
        return fpn_out


class ConvPool(nn.Module):
    def __init__(self, inplane, plane):
        super(ConvPool, self).__init__()
        self.conv = nn.Conv2d(inplane, plane, kernel_size=1, stride=1)
        self.pool = nn.MaxPool2d(kernel_size=2, stride=2)
        self._init_modules()

    def _init_modules(self):
        self.conv.apply(weights_init)

    def forward(self, x):
        out = self.conv(x)
        out = self.pool(out)
        return x, out


class ConvUpsample(nn.Module):
    def __init__(self, inplace, plane):
        super(ConvUpsample, self).__init__()
        self.conv = nn.Conv2d(inplace, plane, kernel_size=1, stride=1)
        self.up_sample = nn.Upsample(scale_factor=2, mode='bilinear')
        self.smooth_conv = nn.Conv2d(plane, plane, kernel_size=1, stride=1)
        self._init_modules()

    def _init_modules(self):
        self.conv.apply(weights_init)
        self.smooth_conv.apply(weights_init)

    def forward(self, x):
        out = self.conv(x)
        out = self.up_sample(out)
        out = self.smooth_conv(out)
        return x, out


class ConvPoolUpsample(nn.Module):
    def __init__(self, inplace, plane):
        super(ConvPoolUpsample, self).__init__()
        self.up_conv = nn.Conv2d(inplace, plane, kernel_size=1, stride=1)
        self.pool_conv = nn.Conv2d(inplace, plane, kernel_size=1, stride=1)
        self.up_sample = nn.Upsample(scale_factor=2, mode='bilinear')
        self.smooth_conv = nn.Conv2d(plane, plane, kernel_size=1, stride=1)
        self.pool = nn.MaxPool2d(kernel_size=2, stride=2)

        self._init_modules()

    def _init_modules(self):
        self.up_conv.apply(weights_init)
        self.smooth_conv.apply(weights_init)
        self.pool_conv.apply(weights_init)

    def forward(self, x):
        up_out = self.up_conv(x)
        pool_out = self.pool_conv(x)
        up_out = self.up_sample(up_out)
        up_out = self.smooth_conv(up_out)
        pool_out = self.pool(pool_out)
        return x, pool_out, up_out


def weave_layers(block, weave_num):
    layers = list()
    for i in range(weave_num):
        if i == 0:
            layers += [ConvPool(block[i], 32)]
        elif i == weave_num - 1:
            layers += [ConvUpsample(block[i], 32)]
        else:
            layers += [ConvPoolUpsample(block[i], 32)]
    return layers


def weave_concat_layers(block, weave_num, channel):
    layers = list()
    for i in range(weave_num):
        if i == 0 or i == weave_num - 1:
            add_channel = channel
        else:
            add_channel = channel * 2
        layers += [
            nn.Conv2d(block[i] + add_channel, 256, kernel_size=1, stride=1)
        ]
    return layers


class WeaveAdapter(nn.Module):
    def __init__(self, block, weave_num):
        super(WeaveAdapter, self).__init__()
        self.trans_layers = nn.ModuleList(trans_layers(block, weave_num))
        self.weave_layers = nn.ModuleList(
            weave_layers([256, 256, 256, 256], weave_num))
        self.weave_concat_layers = nn.ModuleList(
            weave_concat_layers([256, 256, 256, 256], weave_num, 32))
        self.weave_num = weave_num
        self._init_modules()

    def _init_modules(self):
        self.trans_layers.apply(weights_init)
        self.weave_concat_layers.apply(weights_init)

    def forward(self, x):
        trans_layers_list = list()
        weave_out = list()
        for (p, t) in zip(x, self.trans_layers):
            trans_layers_list.append(t(p))
        weave_list = list()
        for (t, w) in zip(trans_layers_list, self.weave_layers):
            weave_list.append(w(t))

        for i in range(self.weave_num):
            if i == 0:
                weave = torch.cat((weave_list[i][0], weave_list[i + 1][-1]), 1)
            elif i == self.weave_num - 1:
                weave = torch.cat((weave_list[i][0], weave_list[i - 1][1]), 1)
            else:
                weave = torch.cat((weave_list[i][0], weave_list[i - 1][1],
                                   weave_list[i + 1][-1]), 1)
            weave = self.weave_concat_layers[i](weave)
            weave_out.append(weave)
        return weave_out