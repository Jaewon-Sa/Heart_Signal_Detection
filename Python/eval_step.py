
import os
import time
import numpy as np
import torch
from torch import nn
from torch import optim
from defaultbox import default
from Detect import Detect

from matplotlib import pyplot as plt

import librosa
import librosa.display

import time
from matplotlib.patches import Rectangle

DIR_PATH="./detection_result"
def get_iou(a,b):
    if len(a)!=4 or len(b)!=4:
        return 0
    a_area = (a[2]-a[0])*(a[3]-a[1])
    b_area = (b[2]-b[0])*(b[3]-b[1])
    
    union_area = a_area + b_area
    
    x1 = max(a[0], b[0])
    x2 = min(a[2], b[2])
    
    y1 = max(a[1], b[1])
    y2 = min(a[3], b[3])
    
    iter_area=0
    w = x2-x1
    h = y2-y1
    if w > 0 and h > 0:
        iter_area = w*h
    
    return iter_area / (union_area-iter_area)

def get_correct_ration(det_bboxes, target_bboxes, batch, n_class, iou_threshold = 0.2):
    '''
    det_bbox 
    type:list
    (cls, batch,data) data= numpy(검출한 객체개수, 5) xmin, ymin, xmax, ymax, cls(confidence)
    
    target_bbox
    type : list
    (batch,객체개수,5) xmin, ymin, xmax, ymax, cls
    정규화 되어있음
    '''
    # confidence type iou index
    TPFN=[[[-1, "FN", -1, -1, t[-1]] for t in t_bboxes] for t_bboxes in target_bboxes]  #tp, fn 저장용 실측 기준
    TPFP=[[[[b_cls_bbox[-1], "FP", -1] for b_cls_bbox in b_cls_bboxes] for b_cls_bboxes in cls_bboxes] for cls_bboxes in det_bboxes]  #tp, fp 저장용 예측 기준

    for i in range(batch):
        for cl in range(1,n_class):
            t_bboxes = np.array(target_bboxes[i])#객체개수, 5
            d_bboxes = det_bboxes[cl][i]
            for t_i, t_bbox in enumerate(t_bboxes):
                if t_bbox[-1]==cl:
                    for d_i, d_bbox in enumerate(d_bboxes):
                        '''
                        1. 한 gt에 여러개 dbox 존재하는경우
                        -> 가장 높은 iou dbox 채택
                        2. 한 dbox에 높은 iou 를 가진 gt가 2개 존재하는경우
                        -> dbox 가 이미 tp이면 넘어감
                        '''
                        iou = get_iou(d_bbox[:-1],t_bbox[:-1])
                        
                        if TPFP[cl][i][d_i][1] == 'TP' and TPFP[cl][i][d_i][-2] != t_i: # 탐색과정에서 dbox가 다른 gt에 tp 일 경우
                            continue
 
                        if iou >= iou_threshold:
                            if TPFN[i][t_i][1]=='TP' and iou > TPFN[i][t_i][2]: # 가장 높은 iou를 가진 값을 채택
                                    past_d_i =  TPFN[i][t_i][-2]
                                    TPFN[i][t_i] = [d_bbox[-1], "TP", iou, d_i, cl]
                                    TPFP[cl][i][d_i] = [d_bbox[-1], "TP", iou, t_i, cl]
                                    TPFP[cl][i][past_d_i][1] = "FP"

                            elif TPFN[i][t_i][1]=='FN':
                                TPFN[i][t_i] = [d_bbox[-1],"TP", iou, d_i, cl]
                                TPFP[cl][i][d_i] = [d_bbox[-1], "TP", iou, t_i, cl]                            
                else:
                    continue
            
    return TPFN, TPFP

def get_ap(tp_n, fp_n, fn_n, detect_list):
    detect_list.sort(key = lambda i : -i[0])
    recall = [] 
    precison = []
    tp_count = 0.
    fp_count = 0.
    ap = 0
    
    for conf, _type in detect_list:
        if _type == "FP":
            fp_count += 1
            
            recall_v = tp_count / (tp_n + fn_n)
            precison_v = tp_count / (tp_count + fp_count)
            
            recall.append(recall_v)
            precison.append(precison_v)
            
        elif _type == "TP": 
            tp_count += 1
            
            recall_v = tp_count / (tp_n + fn_n)
            precison_v = tp_count / (tp_count + fp_count)
            
            recall.append(recall_v)
            precison.append(precison_v)
            
    recall = np.array(recall)
    precison = np.array(precison)
    '''        
    11-point interpolation 방식 
    해당 코드 연산비용은 all point 방식과 비슷, 연습겸 작성한 코드
    '''

    recallRange = [x / 10. for x in range(0,11,1)]
    area = []
    for r in recallRange:
        Recalls = np.argwhere(recall >= r)
        pmax = 0

        if Recalls.size != 0:
            pmax = max(precison[Recalls.min():])

        area.append(pmax)

    ap = sum(area) / 11 # len(recallRange)
    
    return ap

def get_box(output):
    all_boxes = [[[] for _ in range(output.size(0))]
                 for _ in range(output.size(1))]  #all_boxes[cls][image]
        
        
    for i in range(output.size(0)):
        for j in range(1, output.size(1)):#class 종류
            dets = output[i, j, :]
            mask = dets[:, 0].gt(0.).expand(5, dets.size(0)).t()#confidence < 0
            dets = torch.masked_select(dets, mask).view(-1, 5)
            if dets.size(0) == 0: #감지된게 없으면
                continue
            boxes = dets[:, 1:]
            scores = dets[:, 0].cpu().numpy()

            cls_dets = np.hstack((boxes.cpu().numpy(),
                                    scores[:, np.newaxis])).astype(np.float32,
                                                                     copy=False)
            all_boxes[j][i] = cls_dets
            
    return all_boxes

def visualization_step(model, Data_loader, tensor_d, image_size=(300,300), device="cpu"):
    
    if not os.path.exists(DIR_PATH):
        os.makedirs(DIR_PATH)
        print(f"make '{DIR_PATH}' DIR path")
    else:
        print(f"Already '{DIR_PATH}' DIR path")
    
    detect=Detect()#상위 n개에 대한 detection
    tensor_d = tensor_d.to(device)  
    model.to(device)
    
    w, h = image_size #임시
    
    ap = 0
    total_Recall = 0
    total_Precison = 0
    N=0
    for idx, data in enumerate(Data_loader):
        if(idx==10): break
        images = data[0].to(device)
        labels = [label.cpu() for label in data[1]] # batch, 객체개수, 5
        
        #images=images[1]
        #images=torch.unsqueeze(images, 0)
        #labels=[labels[1]]
        
        total_labels=0
        for label in labels:
            total_labels+=len(label)

        with torch.no_grad():
            cls, loc = model(images)
            output = detect.forward(loc, cls, tensor_d, num_classes = cls.size(-1),  bkg_label=0, top_k=200, conf_thresh=0.4, nms_thresh=0.5)
        all_boxes = get_box(output)
        
        
        n_class =  output.size(1)
        images = images.cpu()
        for i, image in enumerate(images):
            plt.figure(figsize=(20,20))
            fig, ax = plt.subplots(figsize=(15, 15))
            # 스펙트로그램 시각화
            if image.shape[-1] == 3:
                x = np.transpose(np.array(image), (1, 2, 0))
                librosa.display.specshow(cv2.cvtColor(np.array(x), cv2.COLOR_BGR2RGB),
                                         sr=4000,
                                         hop_length=800,
                                         n_fft=N_FFT,
                                         win_length=WIN_LENGTH,
                                         cmap='magma')
            else:
                librosa.display.specshow(np.array(image[0]),
                                         sr=8000,
                                         hop_length=800)

            # 레이블 시각화

            for label in labels[i]:
                start, _, end, height, class_id= label
                start *= 300    # 300 = output 이미지 가로 크기
                end *= 300      # 300 = output 이미지 가로 크기
                height *= 300   # 300 = output 이미지 세로 크기
                color = 'red' if class_id == 1 else 'blue'
                ax.add_patch(Rectangle((start, 0), (end - start), height,
                                       edgecolor = color,
                                       facecolor = 'white',
                                       fill=True,
                                       alpha=0.5,

            
            for cl in range(1,n_class):    
                for cls_boxes in all_boxes[cl][i]:
                    start_x, start_y, end_x, end_y = cls_boxes[:-1]
                    
                    start_x = np.clip(start_x*300, 0, 300)
                    start_y = np.clip(start_y*300, 0, 300)
                    end_x = np.clip(end_x*300, 0, 300)
                    end_y = np.clip(end_y*300, 0, 300)
                    
                    color = 'orange' if cl == 1 else 'aqua'
                    ax.add_patch(Rectangle((start_x, start_y), (end_x - start_x), (end_y - start_y),
                                       edgecolor = color,
                                       facecolor = 'white',
                                       fill=True,
                                       alpha=0.5,
                                       lw=4))

                
                

            plt.xlabel("Time")
            plt.ylabel("Frequency")
            plt.savefig(f"detection_result/origin_pred_{idx}_{i}.png")
            
def test_step(model, Data_loader, tensor_d, image_size=(300,300), device="cpu", conf_thresh = 0.4, nms_thresh = 0.5, iou_threshold = 0.5):

    detect=Detect()#상위 n개에 대한 detection
    tensor_d = tensor_d.to(device)  
    model.to(device)
    
    w, h = image_size #임시
    
    ap = 0
    total_Recall = 0
    total_Precison = 0
    
    S1_Recall, S1_Precison, S2_Recall, S2_Precison = [], [], [], []
    
    N=0
    for idx, data in enumerate(Data_loader):
        images = data[0].to(device)
        labels = [label.cpu() for label in data[1]] # batch, 객체개수, 5
        
        #print(labels[1])
        #images=images[1]
        #images=torch.unsqueeze(images, 0)
        #labels=[labels[1]]
        total_labels=0
        for label in labels:
            total_labels+=len(label)

        with torch.no_grad():
            cls, loc = model(images)
            #sorted_tensor, indices = torch.sort(cls[:,:,1], dim=-1, descending=True)
            #print(sorted_tensor[:10])
            #sorted_tensor, indices = torch.sort(cls[:,:,2], dim=-1, descending=True)
            #print(sorted_tensor[:10])
            ''' 
            #For Debug
            #cls = (batch,ddobx,3)
            value, i= torch.max(cls, dim=-1)
            s1 = i == 1
            s2 = i == 2
            bg = i == 0
            #상위 200개에 대한 detection
            #size=(batch, numclass ,200, 5)
            '''

            output = detect.forward(loc, cls, tensor_d, num_classes = cls.size(-1),  bkg_label=0, top_k=200, 
                                    conf_thresh = conf_thresh, nms_thresh = nms_thresh)
            #print(output.size())8,3,200,5
            #ob=output[0,:,:10,:]
            #print(ob.size())
            #print(ob)
        all_boxes = get_box(output)

        TPFN,TPFP = get_correct_ration(all_boxes, labels, batch = output.size(0), n_class = output.size(1), iou_threshold = iou_threshold)
        
        TP_list   = [0,0,0] #total, s1, s2
        TP_list_2 = [0,0,0] #검수용
        
        FN_list = [0,0,0]
        FP_list = [0,0,0]
        
        for batch_TPFN in TPFN:
            for real_det in batch_TPFN:
                con, state = real_det[:2]
                _class = int(real_det[-1])
                if state=="FN":
                    FN_list[_class]+=1
                    
                if state=="TP":
                    TP_list[_class]+=1
                    
        TP_list[0] = TP_list[1] + TP_list[2]
        FN_list[0] = FN_list[1] + FN_list[2]
        
        TPFP_cls_filter=[[] for _ in range(output.size(1))] #n_class AP 계산용
        TPFP_filter=[] # total AP 계산용
        
        for cls_idx, cls_TPFP in enumerate(TPFP):
            if cls_idx == 0:
                continue
            TPFP_cls_list=[]
            for batch_TPFP in cls_TPFP:
                for pred_det in batch_TPFP:
                    if len(pred_det) == 0:
                        continue
                    con, state = pred_det[:2]
                    
                    if state=="FP":
                        FP_list[cls_idx]+=1
                    
                    if state=="TP":
                        TP_list_2[cls_idx]+=1
                        
                    TPFP_cls_list.append(pred_det[:2])
                    TPFP_filter.append(pred_det[:2])
                    
            TPFP_cls_filter[cls_idx] = TPFP_cls_list
        
        TP_list_2[0] = TP_list_2[1] + TP_list_2[2] 
        FP_list[0] = FP_list[1] + FP_list[2]
        
        if TP_list[0] + FP_list[0] == 0:
            continue
        else :
            Precison = TP_list[0] / (TP_list[0] + FP_list[0]) 
        Recall = TP_list[0] / (TP_list[0] + FN_list[0]) #total_labels
        
        total_Recall += Recall
        total_Precison += Precison
        
        if TP_list[1] + FN_list[1] != 0:
            S1_Recall.append(TP_list[1] / (TP_list[1] + FN_list[1]))
        if TP_list[2] + FN_list[2] != 0:
            S2_Recall.append(TP_list[2] / (TP_list[2] + FN_list[2]))
        
        if TP_list[1] + FP_list[1] != 0:
            S1_Precison.append(TP_list[1] / (TP_list[1] + FP_list[1])) 
        if TP_list[2] + FP_list[2] != 0:
            S2_Precison.append(TP_list[2] / (TP_list[2] + FP_list[2])) 
        
        ap +=  get_ap(TP_list[0], FP_list[0], FN_list[0], TPFP_filter)
        N = idx+1
    
    try:
        total_Recall /= N
        total_Precison /= N
    except:
        if N==0:
            total_Recall = 0
            total_Precison = 0
    
    S1_Recall   = float(sum(S1_Recall)/len(S1_Recall)) if len(S1_Recall) != 0 else 0
    S2_Recall   = float(sum(S2_Recall)/len(S2_Recall)) if len(S2_Recall) != 0 else 0
    S1_Precison = float(sum(S1_Precison)/len(S1_Precison)) if len(S1_Precison) != 0 else 0
    S2_Precison = float(sum(S2_Precison)/len(S2_Precison)) if len(S2_Precison) != 0 else 0
    
    mAP = ap / N if N != 0 else 0
    
    print(f"{N}/{len(Data_loader)}")
    return (total_Recall, S1_Recall, S2_Recall,total_Precison, S2_Recall, S2_Precison, mAP)

