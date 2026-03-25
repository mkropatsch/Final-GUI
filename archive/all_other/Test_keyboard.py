import pygame, queue

# Simulated queue (instead of gantry)
q_to_gantry = queue.Queue()

pygame.init()
screen = pygame.display.set_mode((300, 150))
pygame.display.set_caption("WASD → Queue Test")
clock = pygame.time.Clock()

running = True
while running:
    clock.tick(60)
    for event in pygame.event.get():
        if event.type == pygame.QUIT:
            running = False

    keys = pygame.key.get_pressed()
    jx = float(keys[pygame.K_d]) - float(keys[pygame.K_a])
    jy = float(keys[pygame.K_s]) - float(keys[pygame.K_w])

    if jx or jy:
        msg = {"type": "input", "cmd": "xy_motion", "value": (jx, jy)}
        q_to_gantry.put(msg)
        print("Sent:", msg)

pygame.quit()
