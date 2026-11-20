"""Approved long Ghost with integrated cloth arms (Blender Z-up, front -Y).

Matches the standalone model's resting cloth, without its baked animation.
The apps supply their own live motion and audio-driven face controls.
"""
import math

VERSION = 'Ghost Sheet 2.4 Soft Hem'
BODY_DROP = 2.45
RINGS, SEGMENTS = 144, 192


def smooth(a, b, value):
    t = max(0., min(1., (value-a)/(b-a)))
    return t*t*(3-2*t)


def point(u, angle):
    if u < .38:
        h = u/.38*math.pi/2
        return (.86*math.sin(h)*math.cos(angle),
                .6536*math.sin(h)*math.sin(angle), .55+.94*math.cos(h))
    v = (u-.38)/.62
    ripple = (.165*math.cos(7*angle+.65*v)+.045*math.cos(11*angle-.8*v))*smooth(.06,.72,v)
    radius = .86+.14*smooth(0,.9,v)+ripple
    side=abs(math.cos(angle))**10*smooth(0,.12,v)
    arm=.52*math.exp(-((v-.16)/.115)**2)*side
    thumb=.13*math.exp(-((v-.255)/.045)**2)*side
    wing=arm+thumb
    roll = smooth(.83,1,v)
    radius += wing-.13*(1-math.cos(roll*math.pi/2))
    height = (.55-BODY_DROP*v+(.13*math.cos(7*angle+.65)+.035*math.cos(3*angle))*smooth(.35,1,v)
              +.14*math.sin(roll*math.pi/2)+arm*(.64 if math.cos(angle)<0 else .50)+thumb*.25)
    return (radius*math.cos(angle)+.035*v*v*math.sin(angle+.4), radius*.76*math.sin(angle), height)


def mesh():
    vertices = [(0.,0.,1.49)]
    for j in range(1,RINGS+1):
        vertices.extend(point(j/RINGS,i/SEGMENTS*math.tau) for i in range(SEGMENTS))
    faces = [(0,1+i,1+(i+1)%SEGMENTS) for i in range(SEGMENTS)]
    for j in range(RINGS-1):
        for i in range(SEGMENTS):
            a=1+j*SEGMENTS+i; b=1+j*SEGMENTS+(i+1)%SEGMENTS
            faces.append((a,a+SEGMENTS,b+SEGMENTS,b))
    return vertices,faces
